from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from agent import config

SCHEMA_VERSION = 2
AGE_GROUPS = ("18-24", "25-34", "35-44", "45-54", "55+")
INTEREST_KINDS = ("long_term", "project")
QUERY_STYLES = ("keywords", "questions", "mixed")
MIN_INTERESTS, MAX_INTERESTS = 3, 6
MAX_PAST_PROJECTS = 10

_ID_RE = re.compile(r"^[0-9a-f]{8}$")


def is_valid_id(value: Any) -> bool:
    return isinstance(value, str) and bool(_ID_RE.match(value))


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _check_text(value: Any, name: str) -> None:
    _require(isinstance(value, str) and value.strip() != "", f"{name}: expected non-empty string, got {value!r}")


def _check_number(value: Any, name: str, lo: float, hi: float) -> None:
    _require(isinstance(value, (int, float)) and not isinstance(value, bool), f"{name}: expected number, got {value!r}")
    _require(lo <= value <= hi, f"{name}: expected {lo}..{hi}, got {value!r}")


def _check_aware(value: Any, name: str) -> None:
    _require(isinstance(value, datetime), f"{name}: expected datetime, got {value!r}")
    _require(value.tzinfo is not None, f"{name}: datetime must be timezone-aware")


def _parse_dt(value: Any, name: str) -> datetime | None:
    if value is None:
        return None
    _require(isinstance(value, str), f"{name}: expected ISO datetime string, got {value!r}")
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{name}: invalid ISO datetime {value!r}") from None


def _check_keys(data: Any, allowed: set[str], name: str) -> None:
    _require(isinstance(data, dict), f"{name}: expected object, got {type(data).__name__}")
    unknown = set(data) - allowed
    _require(not unknown, f"{name}: unknown fields {sorted(unknown)}")


@dataclass
class Interest:
    topic: str
    description: str
    kind: str
    weight: float
    created_at: datetime
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        _check_text(self.topic, "topic")
        _check_text(self.description, "description")
        _require(self.kind in INTEREST_KINDS, f"kind: expected one of {INTEREST_KINDS}, got {self.kind!r}")
        _check_number(self.weight, "weight", 0.0, 1.0)
        _require(self.weight > 0, "weight: must be greater than 0")
        _check_aware(self.created_at, "created_at")
        if self.kind == "project":
            _check_aware(self.expires_at, "expires_at")
            _require(self.expires_at > self.created_at, "expires_at: must be after created_at")
        else:
            _require(self.expires_at is None, "expires_at: only projects expire")

    def to_dict(self) -> dict:
        return {
            "topic": self.topic,
            "description": self.description,
            "kind": self.kind,
            "weight": self.weight,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Interest:
        _check_keys(data, {"topic", "description", "kind", "weight", "created_at", "expires_at"}, "interest")
        return cls(
            topic=data.get("topic"),
            description=data.get("description"),
            kind=data.get("kind"),
            weight=data.get("weight"),
            created_at=_parse_dt(data.get("created_at"), "created_at"),
            expires_at=_parse_dt(data.get("expires_at"), "expires_at"),
        )


@dataclass
class Behavior:
    query_style: str
    reading_wpm: int
    typo_rate: float
    youtube_affinity: float
    patience: float

    def __post_init__(self) -> None:
        _require(self.query_style in QUERY_STYLES, f"query_style: expected one of {QUERY_STYLES}, got {self.query_style!r}")
        _require(isinstance(self.reading_wpm, int) and not isinstance(self.reading_wpm, bool),
                 f"reading_wpm: expected integer, got {self.reading_wpm!r}")
        _check_number(self.reading_wpm, "reading_wpm", 150, 400)
        _check_number(self.typo_rate, "typo_rate", 0.0, 0.06)
        _check_number(self.youtube_affinity, "youtube_affinity", 0.0, 1.0)
        _check_number(self.patience, "patience", 0.0, 1.0)

    def to_dict(self) -> dict:
        return {
            "query_style": self.query_style,
            "reading_wpm": self.reading_wpm,
            "typo_rate": self.typo_rate,
            "youtube_affinity": self.youtube_affinity,
            "patience": self.patience,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Behavior:
        _check_keys(data, {"query_style", "reading_wpm", "typo_rate", "youtube_affinity", "patience"}, "behavior")
        return cls(**{key: data.get(key) for key in
                      ("query_style", "reading_wpm", "typo_rate", "youtube_affinity", "patience")})


@dataclass
class Persona:
    id: str
    name: str
    age_group: str
    occupation: str
    city: str
    languages: dict[str, float]
    interests: list[Interest]
    behavior: Behavior
    created_at: datetime
    past_projects: list[str] = field(default_factory=list)  # topics of finished projects, oldest first
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require(is_valid_id(self.id), f"id: expected 8 lowercase hex chars, got {self.id!r}")
        _check_text(self.name, "name")
        _require(self.age_group in AGE_GROUPS, f"age_group: expected one of {AGE_GROUPS}, got {self.age_group!r}")
        _check_text(self.occupation, "occupation")
        _require(self.city in config.CITIES, f"city: expected one of {tuple(config.CITIES)}, got {self.city!r}")

        _require(isinstance(self.languages, dict) and self.languages, "languages: expected non-empty object")
        for lang, share in self.languages.items():
            _require(lang in config.LANGUAGES, f"languages: unknown language {lang!r}")
            _check_number(share, f"languages[{lang}]", 0.0, 1.0)
        _require(abs(sum(self.languages.values()) - 1.0) < 1e-3, "languages: shares must sum to 1")

        _require(isinstance(self.interests, list), "interests: expected list")
        _require(MIN_INTERESTS <= len(self.interests) <= MAX_INTERESTS,
                 f"interests: expected {MIN_INTERESTS}..{MAX_INTERESTS}, got {len(self.interests)}")
        _require(all(isinstance(i, Interest) for i in self.interests), "interests: expected Interest items")
        topics = [i.topic.strip().casefold() for i in self.interests]
        _require(len(topics) == len(set(topics)), "interests: duplicate topics")

        _require(isinstance(self.behavior, Behavior), "behavior: expected Behavior")
        _check_aware(self.created_at, "created_at")
        _require(isinstance(self.past_projects, list), "past_projects: expected list")
        _require(len(self.past_projects) <= MAX_PAST_PROJECTS,
                 f"past_projects: at most {MAX_PAST_PROJECTS}, got {len(self.past_projects)}")
        for topic in self.past_projects:
            _check_text(topic, "past_projects item")
        _require(self.schema_version == SCHEMA_VERSION,
                 f"schema_version: expected {SCHEMA_VERSION}, got {self.schema_version!r}")

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "name": self.name,
            "age_group": self.age_group,
            "occupation": self.occupation,
            "city": self.city,
            "languages": dict(self.languages),
            "interests": [i.to_dict() for i in self.interests],
            "behavior": self.behavior.to_dict(),
            "created_at": self.created_at.isoformat(),
            "past_projects": list(self.past_projects),
        }

    @classmethod
    def from_dict(cls, data: dict) -> Persona:
        data = _migrate(data)
        _check_keys(data, {"schema_version", "id", "name", "age_group", "occupation", "city",
                           "languages", "interests", "behavior", "created_at", "past_projects"}, "persona")
        interests = data.get("interests")
        _require(isinstance(interests, list), "interests: expected list")
        return cls(
            id=data.get("id"),
            name=data.get("name"),
            age_group=data.get("age_group"),
            occupation=data.get("occupation"),
            city=data.get("city"),
            languages=data.get("languages"),
            interests=[Interest.from_dict(i) for i in interests],
            behavior=Behavior.from_dict(data.get("behavior")),
            created_at=_parse_dt(data.get("created_at"), "created_at"),
            past_projects=data.get("past_projects"),
            schema_version=data.get("schema_version"),
        )


def _migrate(data: Any) -> Any:
    """Upgrades personas saved by older versions; the file itself is rewritten on the next save."""
    if isinstance(data, dict) and data.get("schema_version") == 1:
        data = {**data, "schema_version": 2, "past_projects": []}
    return data
