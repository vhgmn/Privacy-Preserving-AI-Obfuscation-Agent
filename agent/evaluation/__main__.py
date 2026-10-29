"""Command line for evaluation: python -m agent.evaluation {fingerprint,fingerprint-report,tracker}"""
import argparse
import sys
from datetime import date, datetime

import httpx
from playwright.sync_api import Error as PlaywrightError

from agent.browser.runner import persona_browser
from agent.evaluation import fingerprint, tracker_view
from agent.persona import store as persona_store
from agent.scheduler.state import DayState

MINE = "mine"  # label of the user's own Chrome


def cmd_fingerprint(args) -> int:
    today = date.today()
    if args.mine:
        print("Open this address in your usual Chrome (it only talks to this computer):", flush=True)
        fp = fingerprint.serve_probe(timeout=args.timeout, on_ready=lambda url: print(f"  {url}", flush=True))
        if fp is None:
            print("No answer from the browser; nothing saved.")
            return 1
        print(f"Saved {fingerprint.save(MINE, fp, today)}")
        return 0

    state = DayState.load(today)
    busy = state.busy(datetime.now())
    if busy:
        print(f"Chrome is in use ({busy}); try again after that session.")
        return 1
    state.start("fingerprint audit", datetime.now())
    state.save()
    try:
        for persona in persona_store.list_all():
            print(f"{persona.name}...", flush=True)
            try:
                with persona_browser(persona) as context:
                    fp = fingerprint.collect(context)
            except PlaywrightError as exc:
                print(f"  failed: {str(exc).splitlines()[0][:200]}")
                continue
            print(f"  saved {fingerprint.save(persona.id, fp, today)}")
    finally:
        state = DayState.load(today)  # the daemon may have saved in the meantime
        state.finish()
        state.save()
    return 0


def cmd_report(args) -> int:
    day = date.fromisoformat(args.date) if args.date else date.today()
    fingerprints = fingerprint.load_day(day)
    if len(fingerprints) < 2:
        print(f"Need at least two fingerprints for {day}; found {len(fingerprints)}.")
        return 1
    names = {p.id: p.name for p in persona_store.list_all()}
    names[MINE] = "you (own Chrome)"
    print(fingerprint.format_report(fingerprint.compare(fingerprints), names))
    return 0


def cmd_tracker(args) -> int:
    end = date.fromisoformat(args.date) if args.date else date.today()
    personas = [persona_store.load(args.persona_id)] if args.persona_id else persona_store.list_all()
    for persona in personas:
        events = tracker_view.load_events(persona.id, end, args.days)
        if not events:
            print(f"{persona.name}: no logs in the last {args.days} day(s)")
            continue
        print(f"{persona.name}: asking the local LLM (a minute or two)...", flush=True)
        try:
            result = tracker_view.evaluate(persona, events, end, args.days)
        except httpx.HTTPError as exc:
            print(f"error: cannot reach Ollama ({exc})", file=sys.stderr)
            return 1
        except ValueError as exc:
            print(f"  the LLM did not answer with valid JSON: {exc}")
            continue
        print(tracker_view.format_report(result, persona))
        print(f"  saved {tracker_view.save(result)}\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m agent.evaluation", description="Measure how well decoys hide.")
    sub = parser.add_subparsers(dest="command", required=True)
    fp = sub.add_parser("fingerprint", help="record the browser fingerprint of every persona profile")
    fp.add_argument("--mine", action="store_true", help="record your own Chrome instead, via a local page")
    fp.add_argument("--timeout", type=float, default=300, help="seconds to wait with --mine, default 300")
    fp.set_defaults(func=cmd_fingerprint)
    report = sub.add_parser("fingerprint-report", help="compare the recorded fingerprints")
    report.add_argument("--date", help="YYYY-MM-DD, default today")
    report.set_defaults(func=cmd_report)
    tracker = sub.add_parser("tracker", help="let the local LLM profile each persona from its logs, as a tracker would")
    tracker.add_argument("persona_id", nargs="?", help="default: every persona")
    tracker.add_argument("--days", type=int, default=7, help="how many days of logs, default 7")
    tracker.add_argument("--date", help="last day YYYY-MM-DD, default today")
    tracker.set_defaults(func=cmd_tracker)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
