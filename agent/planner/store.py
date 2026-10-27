import json
import os
from datetime import date
from pathlib import Path

from agent import config
from agent.persona.schema import is_valid_id
from agent.planner.schema import DailyPlan

PLANS_DIR_NAME = "plans"


def _plans_dir() -> Path:
    return config.DATA_DIR / PLANS_DIR_NAME


def _path(persona_id: str, day: date, directory: Path | None) -> Path:
    if not is_valid_id(persona_id):
        raise ValueError(f"invalid persona id {persona_id!r}")
    return (directory or _plans_dir()) / day.isoformat() / f"{persona_id}.json"


def exists(persona_id: str, day: date, directory: Path | None = None) -> bool:
    return _path(persona_id, day, directory).exists()


def save(plan: DailyPlan, directory: Path | None = None) -> Path:
    path = _path(plan.persona_id, plan.date, directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return path


def load(persona_id: str, day: date, directory: Path | None = None) -> DailyPlan:
    path = _path(persona_id, day, directory)
    try:
        return DailyPlan.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"{path.parent.name}/{path.name}: {exc}") from exc
