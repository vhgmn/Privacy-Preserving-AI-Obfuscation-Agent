"""Command line for daily plans: python -m agent.planner {today,show}"""
import argparse
import sys
from datetime import date

import httpx

from agent.persona import store as persona_store
from agent.persona.generator import GenerationError
from agent.planner import store
from agent.planner.planner import plan_day
from agent.planner.schema import DailyPlan


def _describe(plan: DailyPlan, name: str) -> str:
    lines = [f"{plan.persona_id}  {name}  {plan.date}  ({len(plan.sessions)} sessions)"]
    for number, session in enumerate(plan.sessions, 1):
        lines.append(f"  {number}. [{session.language}] {session.topic} ({session.source})")
        for step in session.steps:
            if step.type == "search":
                lines.append(f"       search{'*' if step.replaceable else ' '}  {step.query}")
            elif step.type == "youtube":
                lines.append(f"       youtube  {step.query}")
            elif step.type == "open_result":
                lines.append(f"       open #{step.rank} and read")
    return "\n".join(lines)


def cmd_today(args) -> int:
    personas = persona_store.list_all()
    if not personas:
        print("No personas yet. Run: python -m agent.persona generate")
        return 0
    day, failed = date.today(), 0
    for persona in personas:
        if store.exists(persona.id, day) and not args.force:
            print(f"{persona.id}  {persona.name}: already planned for {day} (use --force to replan)")
            continue
        print(f"Planning {persona.name} ({persona.id})...", flush=True)
        try:
            plan, warnings = plan_day(persona, day)
        except httpx.HTTPError as exc:
            print(f"error: cannot reach Ollama at 127.0.0.1:11434 ({exc}). Is it running?", file=sys.stderr)
            return 1
        except GenerationError as exc:
            print(f"{persona.id}  {persona.name}: error: {exc}", file=sys.stderr)
            failed += 1
            continue
        for warning in warnings:
            print(f"  warning: {warning}")
        store.save(plan)
        print(_describe(plan, persona.name), flush=True)
    return 1 if failed else 0


def cmd_show(args) -> int:
    day = date.fromisoformat(args.date) if args.date else date.today()
    persona = persona_store.load(args.persona_id)
    print(_describe(store.load(args.persona_id, day), persona.name))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m agent.planner", description="Plan decoy browsing sessions.")
    sub = parser.add_subparsers(dest="command", required=True)
    today = sub.add_parser("today", help="plan today for every persona")
    today.add_argument("--force", action="store_true", help="replace plans that already exist")
    today.set_defaults(func=cmd_today)
    show = sub.add_parser("show", help="print one persona's plan")
    show.add_argument("persona_id")
    show.add_argument("--date", help="YYYY-MM-DD, default today")
    show.set_defaults(func=cmd_show)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
