"""Command line for personas: python -m agent.persona {generate,list,show,delete}"""
import argparse
import json
import sys

import httpx

from agent import config
from agent.persona import store
from agent.persona.generator import GenerationError, generate_persona
from agent.persona.schema import Persona


def _summary(p: Persona) -> str:
    lines = [f"{p.id}  {p.name}, {p.occupation}, {p.age_group}, {p.city}"]
    for i in p.interests:
        until = f" (until {i.expires_at:%Y-%m-%d})" if i.expires_at else ""
        lines.append(f"    [{i.kind}] {i.topic}{until}")
    return "\n".join(lines)


def cmd_generate(args) -> int:
    existing = store.list_all()
    count = args.count if args.count is not None else config.PERSONA_COUNT - len(existing)
    if count <= 0:
        print(f"Already have {len(existing)} personas (target {config.PERSONA_COUNT}). Use --count to add more.")
        return 0
    for n in range(1, count + 1):
        print(f"Generating persona {n}/{count} with the local LLM (about a minute)...", flush=True)
        try:
            persona = generate_persona(existing)
        except httpx.HTTPError as exc:
            print(f"error: cannot reach Ollama at 127.0.0.1:11434 ({exc}). Is it running?", file=sys.stderr)
            return 1
        except GenerationError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        store.save(persona)
        existing.append(persona)
        print(_summary(persona), flush=True)
    return 0


def cmd_list(args) -> int:
    personas = store.list_all()
    if not personas:
        print("No personas yet. Run: python -m agent.persona generate")
    for p in personas:
        print(_summary(p))
    return 0


def cmd_show(args) -> int:
    print(json.dumps(store.load(args.id).to_dict(), ensure_ascii=False, indent=2))
    return 0


def cmd_delete(args) -> int:
    store.delete(args.id)
    print(f"Deleted persona {args.id} (its browser profile, if any, is kept).")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m agent.persona", description="Manage decoy personas.")
    sub = parser.add_subparsers(dest="command", required=True)
    gen = sub.add_parser("generate", help=f"create personas up to {config.PERSONA_COUNT}")
    gen.add_argument("--count", type=int, help="create exactly this many more")
    gen.set_defaults(func=cmd_generate)
    sub.add_parser("list", help="list personas").set_defaults(func=cmd_list)
    for name, func, text in (("show", cmd_show, "print one persona as JSON"), ("delete", cmd_delete, "delete one persona")):
        p = sub.add_parser(name, help=text)
        p.add_argument("id")
        p.set_defaults(func=func)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # Armenian names on the Windows console
    sys.exit(main())
