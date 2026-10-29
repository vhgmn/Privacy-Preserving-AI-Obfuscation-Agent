"""Which third parties saw each persona, and on how many sites (phase 5d).

A third-party domain loaded on several of the sites a persona visited can follow it from
site to site and build a profile. One that saw every persona can link them, since all
profiles share one device fingerprint and, until Nym, one IP address.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from agent import config

CROSS_SITE = 2  # seen on this many sites or more: it can follow the persona around


def seen_by(events: list[dict]) -> dict[str, set[str]]:
    """third-party domain -> sites of this persona it was loaded on, over all the given log events."""
    result: dict[str, set[str]] = {}
    for event in events:
        if event.get("event") == "third_parties":
            for domain, sites in (event.get("domains") or {}).items():
                result.setdefault(domain, set()).update(sites)
    return result


@dataclass
class Report:
    end: str
    days: int
    personas: dict[str, dict]  # persona id -> {"sites": n, "third_parties": n, "cross_site": {domain: n sites}}
    shared: dict[str, list[str]]  # cross-site tracker -> persona ids it saw (only those that saw two or more)


def build(per_persona: dict[str, list[dict]], end: str, days: int) -> Report:
    personas, trackers_of = {}, {}
    for persona_id, events in per_persona.items():
        seen = seen_by(events)
        cross = {d: len(s) for d, s in seen.items() if len(s) >= CROSS_SITE}
        personas[persona_id] = {
            "sites": len(set().union(*seen.values())) if seen else 0,
            "third_parties": len(seen),
            "cross_site": dict(sorted(cross.items(), key=lambda item: (-item[1], item[0]))),
        }
        for domain in cross:
            trackers_of.setdefault(domain, []).append(persona_id)
    shared = {d: sorted(ids) for d, ids in sorted(trackers_of.items(), key=lambda i: (-len(i[1]), i[0]))
              if len(ids) >= 2}
    return Report(end, days, personas, shared)


def save(report: Report, directory: Path | None = None) -> Path:
    path = (directory or config.DATA_DIR / "eval" / "trackers") / f"{report.end}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(report), indent=2), encoding="utf-8")
    return path


def format_report(report: Report, names: dict[str, str] | None = None, top: int = 8) -> str:
    names = names or {}
    lines = [f"Third parties in {report.days} day(s) up to {report.end}"]
    for persona_id, data in report.personas.items():
        lines.append(f"  {names.get(persona_id, persona_id)}: {data['third_parties']} third-party domains on "
                     f"{data['sites']} sites; {len(data['cross_site'])} of them on {CROSS_SITE}+ sites")
        for domain, count in list(data["cross_site"].items())[:top]:
            lines.append(f"    {domain:32} {count} sites")
    if not any(data["sites"] for data in report.personas.values()):
        lines.append("  Nothing recorded yet: sessions log third parties from now on.")
        return "\n".join(lines)
    total = len(report.personas)
    everywhere = [d for d, ids in report.shared.items() if len(ids) == total]
    lines.append(f"Trackers that followed two or more personas: {len(report.shared)}"
                 + (f"; all {total}: {', '.join(everywhere[:top])}" if everywhere and total > 1 else ""))
    lines.append("With one device fingerprint and one IP address, such a tracker can tell they are one device.")
    return "\n".join(lines)
