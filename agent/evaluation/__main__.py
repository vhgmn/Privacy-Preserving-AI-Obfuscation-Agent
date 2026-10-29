"""Command line for evaluation: python -m agent.evaluation {fingerprint,fingerprint-report,tracker,classifier,trackers}"""
import argparse
import sqlite3
import sys
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta
from typing import Iterator

import httpx
from playwright.sync_api import Error as PlaywrightError

from agent.browser.runner import persona_browser
from agent.evaluation import classifier, fingerprint, tracker_view, trackers
from agent.persona import store as persona_store
from agent.proxy import ProxyDown
from agent.scheduler.state import DayState

MINE = "mine"  # label of the user's own Chrome


@contextmanager
def agent_busy(who: str) -> Iterator[bool]:
    """Marks the agent busy (see DayState.busy) for the block; yields False if someone else already is."""
    today = date.today()
    state = DayState.load(today)
    busy = state.busy(datetime.now())
    if busy:
        print(f"The agent is busy ({busy}); try again after that.")
        yield False
        return
    state.start(who, datetime.now())
    state.save()
    try:
        yield True
    finally:
        state = DayState.load(today)  # the daemon may have saved in the meantime
        state.finish()
        state.save()


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

    with agent_busy("fingerprint audit") as free:
        if not free:
            return 1
        for persona in persona_store.list_all():
            print(f"{persona.name}...", flush=True)
            try:
                with persona_browser(persona) as context:
                    fp = fingerprint.collect(context)
            except (PlaywrightError, ProxyDown) as exc:
                print(f"  failed: {str(exc).splitlines()[0][:200]}")
                continue
            print(f"  saved {fingerprint.save(persona.id, fp, today)}")
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
    with agent_busy("tracker evaluation") as free:
        return _run_tracker(personas, end, args.days) if free else 1


def _run_tracker(personas, end: date, days: int) -> int:
    for persona in personas:
        events = tracker_view.load_events(persona.id, end, days)
        if not events:
            print(f"{persona.name}: no logs in the last {days} day(s)")
            continue
        print(f"{persona.name}: asking the local LLM (a minute or two)...", flush=True)
        try:
            result = tracker_view.evaluate(persona, events, end, days)
        except httpx.HTTPError as exc:
            print(f"error: cannot reach Ollama ({exc})", file=sys.stderr)
            return 1
        except ValueError as exc:
            print(f"  the LLM did not answer with valid JSON: {exc}")
            continue
        print(tracker_view.format_report(result, persona))
        print(f"  saved {tracker_view.save(result)}\n")
    return 0


def cmd_classifier(args) -> int:
    end = date.fromisoformat(args.date) if args.date else date.today()
    start = datetime.combine(end - timedelta(days=args.days - 1), time.min).astimezone()
    stop = datetime.combine(end + timedelta(days=1), time.min).astimezone()
    decoy = []
    for persona in persona_store.list_all():
        decoy += classifier.sessions(classifier.decoy_visits(tracker_view.load_events(persona.id, end, args.days)))
    try:
        real = classifier.sessions(classifier.read_history(start, stop))
    except (OSError, sqlite3.Error) as exc:
        print(f"error: cannot read your Chrome history ({exc})", file=sys.stderr)
        return 1
    print("Read the time and domain of your visits (nothing of it is saved); training...", flush=True)
    result = classifier.evaluate(classifier.features(real), classifier.features(decoy), end, args.days)
    print(classifier.format_report(result))
    print(f"Saved the summary to {classifier.save(result)}")
    return 0


def cmd_trackers(args) -> int:
    end = date.fromisoformat(args.date) if args.date else date.today()
    personas = persona_store.list_all()
    report = trackers.build({p.id: tracker_view.load_events(p.id, end, args.days) for p in personas},
                            end.isoformat(), args.days)
    print(trackers.format_report(report, {p.id: p.name for p in personas}))
    print(f"Saved the summary to {trackers.save(report)}")
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
    clf = sub.add_parser("classifier", help="can a network observer tell your browsing sessions from the decoys?")
    clf.add_argument("--days", type=int, default=7, help="how many days, default 7")
    clf.add_argument("--date", help="last day YYYY-MM-DD, default today")
    clf.set_defaults(func=cmd_classifier)
    trk = sub.add_parser("trackers", help="which third parties saw each persona, and on how many sites")
    trk.add_argument("--days", type=int, default=7, help="how many days of logs, default 7")
    trk.add_argument("--date", help="last day YYYY-MM-DD, default today")
    trk.set_defaults(func=cmd_trackers)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
