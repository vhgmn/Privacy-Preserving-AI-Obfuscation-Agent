"""Command line for the scheduler: python -m agent.scheduler {run,next,status}"""
import argparse
import sys
from datetime import datetime

from agent.persona import store as persona_store
from agent.scheduler.daemon import Daemon
from agent.scheduler.rhythm import MIN_DAYS_TO_LEARN, idle_seconds
from agent.scheduler.state import DayState


def _print(text: str) -> None:
    print(text, flush=True)


def cmd_run(args) -> int:
    print("Scheduler running. Decoy Chrome opens on the 'Decoy browsing' desktop (Win+Tab). Ctrl+C stops.")
    try:
        Daemon(echo=_print).run_forever()
    except KeyboardInterrupt:
        print("Stopped.")
    return 0


def cmd_next(args) -> int:
    """Runs one pending session right away, on the decoy desktop: for trying things out."""
    daemon = Daemon(echo=_print)
    now = datetime.now()
    state = DayState.load(now.date())
    if not state.prepared:
        if not daemon.prepare_fn(now.date()):
            return 1
        state.prepared = True
        state.save()
    busy = state.busy(now)
    if busy:
        print(f"Chrome is in use ({busy}); try again later.")
        return 1
    pending = daemon.pending(state, now)
    if not pending:
        print("Nothing pending today.")
        return 0
    persona, number, session = pending[0]
    print(f"{persona.name} session {number}: [{session.language}] {session.topic}")
    state.start(f"{persona.name} #{number}", now)
    state.save()
    try:
        outcome = daemon.run_fn(persona, number, session, "full")
    finally:
        state.finish()
        state.save()
    state.mark(persona.id, number, outcome)
    state.save()
    print(f"-> {outcome}")
    return 0 if outcome == "done" else 1


def cmd_status(args) -> int:
    now = datetime.now()
    daemon = Daemon(echo=_print)
    state = DayState.load(now.date())
    waking = sorted(daemon.rhythm.waking_hours(now.date()))
    past_days = sum(1 for day in daemon.rhythm.days if day < now.date().isoformat())
    learned = past_days >= MIN_DAYS_TO_LEARN
    print(f"Now {now:%Y-%m-%d %H:%M}, idle {idle_seconds():.0f} s")
    print(f"Waking hours: {', '.join(f'{h:02d}' for h in waking)}"
          f" ({'learned' if learned else f'default until {MIN_DAYS_TO_LEARN} days are recorded; {past_days} so far'})")
    print(f"Prepared today: {state.prepared}; next session due: {state.next_due or 'when active'}")
    if state.busy(now):
        print(f"Chrome in use: {state.running} since {state.running_since:%H:%M}")
    if state.search_paused_until:
        print(f"Searches paused until {state.search_paused_until:%H:%M} (bot check)")
    names = {p.id: p.name for p in persona_store.list_all()}
    for persona_id, sessions in state.done.items():
        print(f"  {names.get(persona_id, persona_id)}: " + ", ".join(f"#{n} {o}" for n, o in sessions.items()))
    print(f"Pending sessions: {len(daemon.pending(state, now))}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m agent.scheduler", description="Run decoy sessions on a schedule.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="run the scheduler until Ctrl+C").set_defaults(func=cmd_run)
    sub.add_parser("next", help="run one pending session now, on the decoy desktop").set_defaults(func=cmd_next)
    sub.add_parser("status", help="show today's progress").set_defaults(func=cmd_status)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
