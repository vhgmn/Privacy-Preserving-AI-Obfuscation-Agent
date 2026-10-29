"""Optional SOCKS5 proxy per persona, e.g. a local Nym client: python -m agent.proxy {status,set,unset}

Kept in data/proxies.json, which never leaves the machine:
    {"default": "socks5://127.0.0.1:1080"}                 every persona through one client
    {"0a1b2c3d": "socks5://127.0.0.1:1081", ...}           each persona through its own client
A persona's own entry wins over "default". With a proxy set, a persona never goes online
without it: if the proxy does not answer, its sessions do not start (see docs/NYM.md).
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from pathlib import Path
from urllib.parse import urlsplit

from agent import config

DEFAULT = "default"


class ProxyDown(RuntimeError):
    """The persona's proxy does not answer; it must not fall back to the user's own connection."""


def _path(path: Path | None) -> Path:
    return path or config.DATA_DIR / "proxies.json"


def load(path: Path | None = None) -> dict[str, str]:
    try:
        data = json.loads(_path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"{_path(path)}: expected a JSON object")
    return {str(k): str(v) for k, v in data.items()}


def check_url(url: str) -> tuple[str, int]:
    parts = urlsplit(url)
    if parts.scheme != "socks5" or not parts.hostname or not parts.port:
        raise ValueError(f"expected socks5://host:port, got {url!r}")
    return parts.hostname, parts.port


def for_persona(persona_id: str, path: Path | None = None) -> str | None:
    data = load(path)
    return data.get(persona_id) or data.get(DEFAULT)


def reachable(url: str, timeout: float = 3.0) -> bool:
    """Whether something listens at the proxy's address. Connects locally only; sends nothing."""
    host, port = check_url(url)
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def required(persona_id: str, path: Path | None = None) -> str | None:
    """The persona's proxy, checked; None if it has none. Raises ProxyDown if it is set but silent."""
    url = for_persona(persona_id, path)
    if url and not reachable(url):
        raise ProxyDown(f"proxy {url} does not answer")
    return url


def save(data: dict[str, str], path: Path | None = None) -> None:
    target = _path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, target)


# --- command line ---

def main(argv: list[str] | None = None) -> int:
    from agent.persona import store as persona_store

    parser = argparse.ArgumentParser(prog="python -m agent.proxy", description="SOCKS5 proxy per persona.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="each persona's proxy and whether it answers")
    set_ = sub.add_parser("set", help="route a persona (or 'default': all) through a proxy")
    set_.add_argument("who", help="persona id or 'default'")
    set_.add_argument("url", help="socks5://127.0.0.1:1080")
    unset = sub.add_parser("unset", help="remove a proxy setting")
    unset.add_argument("who")
    args = parser.parse_args(argv)

    personas = persona_store.list_all()
    known = {p.id for p in personas} | {DEFAULT}
    data = load()
    if args.command == "set":
        if args.who not in known:
            print(f"error: unknown persona {args.who!r}", file=sys.stderr)
            return 1
        try:
            check_url(args.url)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        data[args.who] = args.url
        save(data)
        print(f"{args.who}: {args.url}" + ("" if reachable(args.url) else "  (not answering yet)"))
    elif args.command == "unset":
        data.pop(args.who, None)
        save(data)
        print(f"{args.who}: no proxy")
    else:
        if not data:
            print("No proxies set: every persona uses your own connection.")
        for persona in personas:
            url = for_persona(persona.id)
            state = "direct (your own connection)" if not url else f"{url} {'OK' if reachable(url) else 'NOT ANSWERING'}"
            print(f"  {persona.name} ({persona.id}): {state}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
