"""Command line for evaluation: python -m agent.evaluation {fingerprint,fingerprint-report}"""
import argparse
import sys
from datetime import date, datetime

from playwright.sync_api import Error as PlaywrightError

from agent.browser.runner import persona_browser
from agent.evaluation import fingerprint
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
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
