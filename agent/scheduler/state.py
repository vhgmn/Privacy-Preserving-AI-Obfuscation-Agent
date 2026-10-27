"""What has been done today, so a restarted daemon never repeats a session."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from agent import config


@dataclass
class DayState:
    day: date
    prepared: bool = False  # personas refreshed and today's plans made
    done: dict[str, dict[str, str]] = field(default_factory=dict)  # persona_id -> {session number: outcome}
    paused_personas: list[str] = field(default_factory=list)  # hit a bot check today
    search_paused_until: datetime | None = None
    next_due: datetime | None = None

    def mark(self, persona_id: str, number: int, outcome: str) -> None:
        self.done.setdefault(persona_id, {})[str(number)] = outcome

    def is_done(self, persona_id: str, number: int) -> bool:
        return str(number) in self.done.get(persona_id, {})

    @staticmethod
    def path(day: date, directory: Path | None = None) -> Path:
        return (directory or config.DATA_DIR / "state") / f"{day.isoformat()}.json"

    def to_dict(self) -> dict:
        return {
            "day": self.day.isoformat(),
            "prepared": self.prepared,
            "done": self.done,
            "paused_personas": self.paused_personas,
            "search_paused_until": self.search_paused_until.isoformat() if self.search_paused_until else None,
            "next_due": self.next_due.isoformat() if self.next_due else None,
        }

    @classmethod
    def load(cls, day: date, directory: Path | None = None) -> DayState:
        try:
            data = json.loads(cls.path(day, directory).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return cls(day)
        parse = lambda v: datetime.fromisoformat(v) if v else None  # noqa: E731
        return cls(day=day, prepared=bool(data.get("prepared")), done=data.get("done") or {},
                   paused_personas=list(data.get("paused_personas") or []),
                   search_paused_until=parse(data.get("search_paused_until")), next_due=parse(data.get("next_due")))

    def save(self, directory: Path | None = None) -> None:
        path = self.path(self.day, directory)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        os.replace(tmp, path)
