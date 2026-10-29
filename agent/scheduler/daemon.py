"""The scheduler loop: prepares each day, then runs planned sessions at human-looking times."""
from __future__ import annotations

import random
import time
from datetime import date, datetime, timedelta
from typing import Callable

import httpx
from playwright.sync_api import Error as PlaywrightError

from agent.browser.runner import SessionLog, persona_browser, run_session
from agent.persona import store as persona_store
from agent.persona.generator import GenerationError, refresh_persona
from agent.persona.schema import Persona
from agent.planner import store as plan_store
from agent.planner.planner import plan_day
from agent.planner.schema import Session
from agent.scheduler import notify
from agent.scheduler.rhythm import Rhythm, idle_seconds
from agent.scheduler.state import DayState
from agent.scheduler.timing import decide, is_active, next_gap, remaining_waking_minutes, short_version

TICK_SECONDS = 60
SEARCH_PAUSE = timedelta(hours=3)  # after a bot check, no searches at all for a while
PREPARE_RETRY = timedelta(minutes=30)

Pending = tuple[Persona, int, Session]


class Daemon:
    def __init__(self, rng: random.Random | None = None, clock: Callable[[], datetime] = datetime.now,
                 idle: Callable[[], float] = idle_seconds, echo: Callable[[str], None] = print,
                 run_fn: Callable[[Persona, int, Session, str], str] | None = None,
                 prepare_fn: Callable[[date], bool] | None = None,
                 notify_fn: Callable[[str, str], bool] = notify.toast):
        self.rng = rng or random.SystemRandom()
        self.clock, self.idle, self.echo = clock, idle, echo
        self.run_fn = run_fn or self._run_in_chrome
        self.prepare_fn = prepare_fn or self._prepare
        self.notify_fn = notify_fn
        self.rhythm = Rhythm.load()
        self._prepare_retry_at: datetime | None = None

    # --- one minute of the loop ---

    def tick(self) -> None:
        now = self.clock()
        idle = self.idle()
        if self.rhythm.record(now, is_active(idle)):
            self.rhythm.save()
        waking = self.rhythm.waking_hours(now.date())
        if now.hour not in waking:
            return
        state = DayState.load(now.date())
        if state.busy(now):
            return  # e.g. the fingerprint audit has Chrome, or an evaluation is using the LLM
        if not state.prepared:
            if self._prepare_retry_at and now < self._prepare_retry_at:
                return
            if not self.prepare_today(state, now):
                self._prepare_retry_at = now + PREPARE_RETRY
                return

        pending = self.pending(state, now)
        if not pending:
            return
        mode = decide(now, idle, waking, state.next_due, self.rng)
        if mode is None:
            if state.next_due is None or now >= state.next_due:
                # due, but the user is away and this one is skipped: roll again after a new pause
                state.next_due = now + next_gap(self.rng, len(pending), remaining_waking_minutes(now, waking))
                state.save()
            return

        persona, number, session = self.rng.choice(pending)
        if mode == "short":
            session = short_version(session)
        self.echo(f"{now:%H:%M} {persona.name} session {number} ({mode}): [{session.language}] {session.topic}")
        state.start(f"{persona.name} #{number}", now)
        state.save()
        try:
            outcome = self.run_fn(persona, number, session, mode)
        finally:
            state.finish()
            state.save()
        self.echo(f"        -> {outcome}")

        state.mark(persona.id, number, outcome)
        end = self.clock()
        if outcome == "blocked":
            state.paused_personas.append(persona.id)
            state.search_paused_until = end + SEARCH_PAUSE
            self.notify_fn("Decoy agent paused",
                           f"{persona.name}: the site showed a bot check. That persona rests until tomorrow; "
                           f"no searches for {SEARCH_PAUSE.seconds // 3600} hours.")
        state.next_due = end + next_gap(self.rng, len(pending) - 1, remaining_waking_minutes(end, waking))
        state.save()

    def prepare_today(self, state: DayState, now: datetime) -> bool:
        """Runs the preparation marked as busy: it uses the local LLM, which must not be shared."""
        state.start("preparing the day", now)
        state.save()
        try:
            ok = self.prepare_fn(now.date())
        finally:
            state.finish()
            state.save()
        if ok:
            state.prepared = True
            state.save()
        return ok

    def pending(self, state: DayState, now: datetime) -> list[Pending]:
        if state.search_paused_until and now < state.search_paused_until:
            return []  # every session starts with a search
        result = []
        for persona in persona_store.list_all():
            if persona.id in state.paused_personas or not plan_store.exists(persona.id, state.day):
                continue
            plan = plan_store.load(persona.id, state.day)
            result += [(persona, n, s) for n, s in enumerate(plan.sessions, 1) if not state.is_done(persona.id, n)]
        return result

    def run_forever(self) -> None:
        while True:
            try:
                self.tick()
            except Exception as exc:  # keep the daemon alive; the next tick starts fresh
                self.echo(f"error in scheduler tick: {type(exc).__name__}: {exc}")
            time.sleep(TICK_SECONDS)

    # --- real work (replaced by fakes in tests) ---

    def _prepare(self, day: date) -> bool:
        """Refreshes expired projects and plans the day. False if Ollama could not be reached."""
        self.echo(f"Preparing {day}: refreshing personas and planning (takes a few minutes)...")
        personas = persona_store.list_all()
        for index, persona in enumerate(personas):
            try:
                updated = refresh_persona(persona, personas[:index] + personas[index + 1:])
                if updated:
                    persona_store.save(updated)
                    personas[index] = persona = updated
                if not plan_store.exists(persona.id, day):
                    plan, warnings = plan_day(persona, day)
                    plan_store.save(plan)
                    self.echo(f"  {persona.name}: {len(plan.sessions)} sessions planned")
                    for warning in warnings:
                        self.echo(f"    warning: {warning}")
            except httpx.HTTPError as exc:
                self.echo(f"  cannot reach Ollama ({exc}); will retry in {PREPARE_RETRY.seconds // 60} minutes")
                return False
            except GenerationError as exc:
                self.echo(f"  {persona.name}: could not prepare: {exc}")
        return True

    def _run_in_chrome(self, persona: Persona, number: int, session: Session, mode: str) -> str:
        log = SessionLog(persona.id, self.clock().date(), number)
        log.write("scheduled", mode=mode, idle_seconds=round(self.idle()))
        try:
            with persona_browser(persona, on_own_desktop=True, echo=self.echo) as context:
                return run_session(context, persona, session, log, echo=self.echo, browse=mode != "short")
        except PlaywrightError as exc:
            message = str(exc).splitlines()[0][:200]
            self.echo(f"  could not run: {message}")
            log.write("launch_failed", error=message)
            return "failed"
