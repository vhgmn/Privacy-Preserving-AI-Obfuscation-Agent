import json
import os
from pathlib import Path

from agent import config
from agent.persona.schema import Persona, is_valid_id


def _path(persona_id: str, directory: Path) -> Path:
    # The id becomes a filename, so reject anything that could escape the directory.
    if not is_valid_id(persona_id):
        raise ValueError(f"invalid persona id {persona_id!r}")
    return directory / f"{persona_id}.json"


def save(persona: Persona, directory: Path | None = None) -> Path:
    directory = directory or config.PERSONAS_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = _path(persona.id, directory)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(persona.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)  # atomic: a crash never leaves a half-written persona
    return path


def load(persona_id: str, directory: Path | None = None) -> Persona:
    path = _path(persona_id, directory or config.PERSONAS_DIR)
    try:
        return Persona.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"{path.name}: {exc}") from exc


def list_all(directory: Path | None = None) -> list[Persona]:
    directory = directory or config.PERSONAS_DIR
    if not directory.exists():
        return []
    return [load(path.stem, directory) for path in sorted(directory.glob("*.json"))]


def delete(persona_id: str, directory: Path | None = None) -> None:
    """Removes the persona file only; its Chrome profile under profiles/ is left alone."""
    _path(persona_id, directory or config.PERSONAS_DIR).unlink()
