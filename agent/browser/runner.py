"""Runs one planned session in the persona's own Chrome profile and logs what happened."""
from __future__ import annotations

import json
import random
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterator

from playwright.sync_api import BrowserContext
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from agent import config
from agent.browser import sites
from agent.persona.schema import Persona
from agent.planner.schema import Session

CHROME_ARGS = [
    "--disable-blink-features=AutomationControlled",  # otherwise navigator.webdriver is true
    "--mute-audio",  # videos play silently
]


def _logs_dir() -> Path:
    return config.DATA_DIR / "logs"


class SessionLog:
    """Appends one JSON line per event to data/logs/<date>/<persona_id>.jsonl (kept local, for evaluation)."""

    def __init__(self, persona_id: str, day: date, session_number: int, directory: Path | None = None):
        self.path = (directory or _logs_dir()) / day.isoformat() / f"{persona_id}.jsonl"
        self.session_number = session_number

    def write(self, event: str, **fields) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        record = {"time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                  "session": self.session_number, "event": event, **fields}
        with self.path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")


@contextmanager
def persona_browser(persona: Persona) -> Iterator[BrowserContext]:
    """Real Chrome, visible, with a persistent profile that belongs to this persona only."""
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(config.PROFILES_DIR / persona.id),
            channel="chrome",
            headless=False,
            args=CHROME_ARGS,
            ignore_default_args=["--enable-automation"],
            accept_downloads=False,
            no_viewport=True,  # use the real window size, like a person's browser
        )
        try:
            yield context
        finally:
            context.close()


def webdriver_flag(context: BrowserContext) -> object:
    page = context.pages[0] if context.pages else context.new_page()
    return page.evaluate("navigator.webdriver")


def run_session(context: BrowserContext, persona: Persona, session: Session, log: SessionLog,
                rng: random.Random | None = None, echo=print) -> str:
    """Executes the steps in order. Returns "done", "blocked" or "failed".

    A failed step (slow site, missing result) skips ahead to the next search, as a
    person would; a bot check stops the whole session.
    """
    rng = rng or random.SystemRandom()
    page = context.pages[0] if context.pages else context.new_page()
    mouse = sites.Mouse(page, rng)
    behavior = persona.behavior
    log.write("session_start", topic=session.topic, language=session.language,
              webdriver=page.evaluate("navigator.webdriver"))
    skip_to_next_search = False
    failures = 0
    visited: set[str] = set()  # a person does not reread the page they just read
    try:
        for step in session.steps:
            if skip_to_next_search and step.type not in ("search", "youtube"):
                continue
            skip_to_next_search = False
            try:
                if step.type == "search":
                    echo(f"  search: {step.query}")
                    sites.ddg_search(page, mouse, step.query, behavior.typo_rate, rng)
                    log.write("search", query=step.query)
                elif step.type == "open_result":
                    url = sites.open_result(page, mouse, step.rank, visited)
                    echo(f"  open #{step.rank}: {url}")
                    log.write("open_result", rank=step.rank, url=url)
                elif step.type == "read":
                    seconds = sites.read_page(page, behavior.reading_wpm, behavior.patience, rng)
                    log.write("read", url=page.url, seconds=round(seconds))
                    sites.back_to_results(page)
                elif step.type == "youtube":
                    echo(f"  youtube: {step.query}")
                    url, seconds = sites.youtube(page, mouse, step.query, behavior.typo_rate,
                                                 behavior.youtube_affinity, rng)
                    log.write("youtube", query=step.query, url=url, seconds=round(seconds))
            except PlaywrightError as exc:
                failures += 1
                message = str(exc).splitlines()[0][:200]
                echo(f"  step failed ({step.type}): {message}")
                log.write("step_failed", step=step.type, error=message)
                skip_to_next_search = True
    except sites.Blocked as exc:
        echo(f"  STOPPED: {exc}. Not trying to get past it.")
        log.write("blocked", url=page.url, reason=str(exc))
        return "blocked"
    outcome = "failed" if failures and failures >= len([s for s in session.steps if s.type == "search"]) else "done"
    log.write("session_end", outcome=outcome, failures=failures)
    return outcome
