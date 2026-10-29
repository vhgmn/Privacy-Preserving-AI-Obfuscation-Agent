from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

from agent import config
from agent.persona.schema import _check_aware, _check_keys, _check_text, _parse_dt, _require, is_valid_id

SCHEMA_VERSION = 2
SOURCES = ("long_term", "project", "past_project", "habit")  # habit: only favourite sites, no search
ENGINES = ("duckduckgo",)
STEP_TYPES = ("search", "open_result", "read", "youtube", "visit")  # visit: straight to a favourite site
MIN_SESSIONS, MAX_SESSIONS = 2, 5
MAX_RANK = 5
MAX_QUERY_WORDS = 8

_ARMENIAN = re.compile(r"[Ա-և]")
_CYRILLIC = re.compile(r"[Ѐ-ӿ]")
_LATIN = re.compile(r"[A-Za-z]")
# Letters outside Armenian, Cyrillic and basic Latin, e.g. the Turkish "ű" a model slipped into Armenian.
_FOREIGN = re.compile(r"[^\W\d_A-Za-zԱ-ևЀ-ӿ]")


def script_matches(text: str, language: str) -> bool:
    """A query must look like it was typed in its language; Latin is allowed in
    Armenian and Russian queries for brand and model names."""
    if _FOREIGN.search(text):
        return False
    if language == "hy":
        return bool(_ARMENIAN.search(text)) and not _CYRILLIC.search(text)
    if language == "ru":
        return bool(_CYRILLIC.search(text)) and not _ARMENIAN.search(text)
    return bool(_LATIN.search(text)) and not _ARMENIAN.search(text) and not _CYRILLIC.search(text)


def check_query(query, language: str, name: str = "query") -> None:
    _check_text(query, name)
    _require(len(query.split()) <= MAX_QUERY_WORDS, f"{name}: more than {MAX_QUERY_WORDS} words: {query!r}")
    _require(script_matches(query, language), f"{name}: not written in {language!r}: {query!r}")


@dataclass
class Step:
    type: str
    query: str | None = None
    rank: int | None = None
    replaceable: bool = False  # a planned follow-up the browser agent may swap for one based on real results
    url: str | None = None  # visit only

    def __post_init__(self) -> None:
        _require(self.type in STEP_TYPES, f"step type: expected one of {STEP_TYPES}, got {self.type!r}")
        if self.type in ("search", "youtube"):
            _check_text(self.query, f"{self.type}.query")
        else:
            _require(self.query is None, f"{self.type}: takes no query")
        if self.type == "open_result":
            _require(isinstance(self.rank, int) and not isinstance(self.rank, bool) and 1 <= self.rank <= MAX_RANK,
                     f"open_result.rank: expected 1..{MAX_RANK}, got {self.rank!r}")
        else:
            _require(self.rank is None, f"{self.type}: takes no rank")
        if self.type == "visit":
            _require(isinstance(self.url, str) and self.url.startswith(("http://", "https://")),
                     f"visit.url: expected http(s) address, got {self.url!r}")
        else:
            _require(self.url is None, f"{self.type}: takes no url")
        _require(isinstance(self.replaceable, bool), "replaceable: expected bool")
        _require(not self.replaceable or self.type == "search", f"{self.type}: only searches are replaceable")

    def to_dict(self) -> dict:
        data = {"type": self.type}
        if self.query is not None:
            data["query"] = self.query
        if self.rank is not None:
            data["rank"] = self.rank
        if self.replaceable:
            data["replaceable"] = True
        if self.url is not None:
            data["url"] = self.url
        return data

    @classmethod
    def from_dict(cls, data: dict) -> Step:
        _check_keys(data, {"type", "query", "rank", "replaceable", "url"}, "step")
        return cls(type=data.get("type"), query=data.get("query"), rank=data.get("rank"),
                   replaceable=data.get("replaceable", False), url=data.get("url"))


@dataclass
class Session:
    topic: str
    source: str
    language: str
    engine: str
    steps: list[Step]

    def __post_init__(self) -> None:
        _check_text(self.topic, "topic")
        _require(self.source in SOURCES, f"source: expected one of {SOURCES}, got {self.source!r}")
        _require(self.language in config.LANGUAGES, f"language: expected one of {config.LANGUAGES}, got {self.language!r}")
        _require(self.engine in ENGINES, f"engine: expected one of {ENGINES}, got {self.engine!r}")
        _require(isinstance(self.steps, list) and self.steps, "steps: expected non-empty list")
        _require(all(isinstance(s, Step) for s in self.steps), "steps: expected Step items")
        first = self.steps[0]
        _require((first.type == "search" and not first.replaceable) or first.type == "visit",
                 "steps: must start with a fixed search or a visit")
        if self.source == "habit":
            _require(all(s.type in ("visit", "read") for s in self.steps), "habit session: only visits and reads")
        previous, on_results = None, False
        for step in self.steps:
            if step.type == "open_result":
                _require(on_results and previous.type in ("search", "read"),
                         "open_result: must follow a search, or a read of a search result")
            if step.type == "read":
                _require(previous is not None and previous.type in ("open_result", "visit"),
                         "read: must follow open_result or visit")
            if step.type in ("search", "youtube"):
                check_query(step.query, self.language, f"{step.type}.query")
            if step.type in ("search", "visit", "youtube"):
                on_results = step.type == "search"
            previous = step
        searches = [s.query.casefold() for s in self.steps if s.type == "search"]
        _require(len(searches) == len(set(searches)), "steps: duplicate search queries")

    def to_dict(self) -> dict:
        return {"topic": self.topic, "source": self.source, "language": self.language, "engine": self.engine,
                "steps": [s.to_dict() for s in self.steps]}

    @classmethod
    def from_dict(cls, data: dict) -> Session:
        _check_keys(data, {"topic", "source", "language", "engine", "steps"}, "session")
        steps = data.get("steps")
        _require(isinstance(steps, list), "steps: expected list")
        return cls(topic=data.get("topic"), source=data.get("source"), language=data.get("language"),
                   engine=data.get("engine"), steps=[Step.from_dict(s) for s in steps])


@dataclass
class DailyPlan:
    persona_id: str
    date: date
    created_at: datetime
    sessions: list[Session]
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require(is_valid_id(self.persona_id), f"persona_id: invalid {self.persona_id!r}")
        _require(isinstance(self.date, date) and not isinstance(self.date, datetime), "date: expected date")
        _check_aware(self.created_at, "created_at")
        _require(isinstance(self.sessions, list) and MIN_SESSIONS <= len(self.sessions) <= MAX_SESSIONS,
                 f"sessions: expected {MIN_SESSIONS}..{MAX_SESSIONS}")
        _require(all(isinstance(s, Session) for s in self.sessions), "sessions: expected Session items")
        _require(self.schema_version == SCHEMA_VERSION,
                 f"schema_version: expected {SCHEMA_VERSION}, got {self.schema_version!r}")

    def to_dict(self) -> dict:
        return {"schema_version": self.schema_version, "persona_id": self.persona_id,
                "date": self.date.isoformat(), "created_at": self.created_at.isoformat(),
                "sessions": [s.to_dict() for s in self.sessions]}

    @classmethod
    def from_dict(cls, data: dict) -> DailyPlan:
        _check_keys(data, {"schema_version", "persona_id", "date", "created_at", "sessions"}, "plan")
        if data.get("schema_version") == 1:  # version 2 only added visits; old plans are valid as they are
            data = {**data, "schema_version": 2}
        sessions = data.get("sessions")
        _require(isinstance(sessions, list), "sessions: expected list")
        raw_date = data.get("date")
        try:
            plan_date = date.fromisoformat(raw_date)
        except (TypeError, ValueError):
            raise ValueError(f"date: invalid {raw_date!r}") from None
        return cls(persona_id=data.get("persona_id"), date=plan_date,
                   created_at=_parse_dt(data.get("created_at"), "created_at"),
                   sessions=[Session.from_dict(s) for s in sessions], schema_version=data.get("schema_version"))
