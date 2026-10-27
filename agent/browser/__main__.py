"""Command line for the browser agent: python -m agent.browser {check,run}"""
import argparse
import sys
from datetime import date

from agent.browser.runner import SessionLog, persona_browser, run_session, webdriver_flag
from agent.persona import store as persona_store
from agent.planner import store as plan_store


def cmd_check(args) -> int:
    persona = persona_store.load(args.persona_id)
    with persona_browser(persona) as context:
        flag = webdriver_flag(context)
    print(f"{persona.name}: navigator.webdriver = {flag!r} ({'OK' if not flag else 'visible to sites'})")
    return 0 if not flag else 1


def cmd_run(args) -> int:
    persona = persona_store.load(args.persona_id)
    day = date.fromisoformat(args.date) if args.date else date.today()
    plan = plan_store.load(persona.id, day)
    if not 1 <= args.session <= len(plan.sessions):
        print(f"error: the plan for {day} has sessions 1..{len(plan.sessions)}", file=sys.stderr)
        return 1
    session = plan.sessions[args.session - 1]
    print(f"{persona.name}, session {args.session}: [{session.language}] {session.topic}", flush=True)
    log = SessionLog(persona.id, day, args.session)
    with persona_browser(persona) as context:
        outcome = run_session(context, persona, session, log, echo=lambda text: print(text, flush=True))
    print(f"Session {outcome}. Log: {log.path}")
    return 0 if outcome == "done" else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m agent.browser", description="Run planned decoy sessions.")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="open the persona's Chrome and report navigator.webdriver")
    check.add_argument("persona_id")
    check.set_defaults(func=cmd_check)
    run = sub.add_parser("run", help="run one session of today's plan")
    run.add_argument("persona_id")
    run.add_argument("--session", type=int, default=1, help="session number, default 1")
    run.add_argument("--date", help="plan date YYYY-MM-DD, default today")
    run.set_defaults(func=cmd_run)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
