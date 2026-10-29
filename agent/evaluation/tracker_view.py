"""Tracker's Eye View: the local LLM plays a tracker that only sees what a tracker could see.

It profiles each persona from its logs, rates how automated it looks and flags sensitive
topics; then a separate judge call checks the profile against the persona's real
interests. The planned topic and language in the logs are the answer, so they are never
shown to the tracker.
"""
from __future__ import annotations

import json
import statistics
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable
from urllib.parse import unquote, urlsplit

from agent import config, llm
from agent.persona.schema import AGE_GROUPS, Persona

LLM = Callable[..., str]  # same signature as llm.generate
OPTIONS = {"temperature": 0, "num_ctx": 8192}
MAX_VIEW_LINES = 250  # keeps the prompt inside the context window; the newest lines are kept
MAX_INTERESTS, MAX_PROJECTS = 6, 3
# Short keys for config.EXCLUDED_TOPICS, in the same order, so answers can be checked by code.
SENSITIVE_KEYS = ("health", "sexuality", "politics", "illegal", "debt", "religion")
VIEWS = {
    "ads": "an advertising network whose scripts run on the pages this visitor read and on YouTube",
    "search": "a search engine that received this visitor's searches",
}


def load_events(persona_id: str, end: date, days: int, directory: Path | None = None) -> list[dict]:
    root = directory or config.DATA_DIR / "logs"
    events = []
    for offset in range(days - 1, -1, -1):
        path = root / (end - timedelta(days=offset)).isoformat() / f"{persona_id}.jsonl"
        if path.exists():
            events += [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return events


def _when(event: dict) -> str:
    return datetime.fromisoformat(event["time"]).astimezone().strftime("%a %Y-%m-%d %H:%M")


def _page(url: str) -> str:
    parts = urlsplit(url)
    return (parts.netloc.removeprefix("www.") + unquote(parts.path).rstrip("/"))[:100]


def build_view(events: list[dict], view: str) -> list[str]:
    """What one kind of observer recorded, one line per event, oldest first."""
    lines = []
    for event in events:
        kind = event.get("event")
        if view == "search" and kind == "search":
            lines.append(f"{_when(event)}  searched: {event['query']}")
        elif view == "ads" and kind == "read" and "duckduckgo.com" not in event.get("url", ""):
            title = f' "{event["title"]}"' if event.get("title") else ""
            lines.append(f"{_when(event)}  read {_page(event['url'])}{title} for {event.get('seconds', '?')} s")
        elif view == "ads" and kind == "youtube":
            lines.append(f"{_when(event)}  YouTube search \"{event['query']}\", "
                         f"watched a video for {event.get('seconds', '?')} s")
    return lines[-MAX_VIEW_LINES:]


# --- signs of automation that code measures better than a small LLM ---

def bot_signals(events: list[dict]) -> dict:
    starts = [datetime.fromisoformat(e["time"]).astimezone() for e in events if e.get("event") == "session_start"]
    gaps = [(b - a).total_seconds() / 60 for a, b in zip(starts, starts[1:])]
    reads = [e["seconds"] for e in events if e.get("event") == "read" and isinstance(e.get("seconds"), (int, float))]
    urls = Counter(e["url"] for e in events if e.get("event") == "read")
    queries = Counter(e["query"] for e in events if e.get("event") == "search")
    return {
        "sessions": len(starts),
        "gap_minutes_median": round(statistics.median(gaps)) if gaps else None,
        # real browsing is bursty: the spread of pauses is about as large as their mean or larger
        "gap_variation": round(statistics.pstdev(gaps) / statistics.mean(gaps), 2) if len(gaps) > 1 else None,
        "night_share": round(sum(1 for s in starts if s.hour < 6) / len(starts), 2) if starts else None,
        "read_seconds_median": round(statistics.median(reads)) if reads else None,
        # people never stay exactly the same number of seconds on many pages; code with a limit does
        "same_read_time_share": round(Counter(reads).most_common(1)[0][1] / len(reads), 2) if len(reads) >= 3 else None,
        "repeated_pages": sum(n - 1 for n in urls.values() if n > 1),
        "repeated_queries": sum(n - 1 for n in queries.values() if n > 1),
        "searches_on_results_page_read": sum(1 for e in events if e.get("event") == "read"
                                             and "duckduckgo.com" in e.get("url", "")),
    }


# --- the LLM as tracker, then as judge ---

def _ask_json(llm_fn: LLM, prompt: str) -> dict:
    data = json.loads(llm_fn(prompt, json_mode=True, options=OPTIONS))
    if not isinstance(data, dict):
        raise ValueError("expected a JSON object")
    return data


def _strings(value, limit: int | None = None) -> list[str]:
    """Clean list of short phrases; drops copies of the prompt's own wording, which small models echo."""
    items, seen = [], set()
    for v in value if isinstance(value, list) else []:
        text = str(v).strip()
        if not text or "|" in text or text.casefold() in ("unknown", "none", "n/a") or text.casefold() in seen:
            continue
        seen.add(text.casefold())
        items.append(text)
    return items[:limit]


def _one_of(value, allowed) -> str:
    return value if isinstance(value, str) and value in allowed else "unknown"


def _free_text(value) -> str:
    text = value.strip() if isinstance(value, str) else ""
    return text if text and "|" not in text and " or unknown" not in text else "unknown"


def clean_profile(raw: dict) -> dict:
    score = raw.get("bot_score")
    valid_score = isinstance(score, int) and not isinstance(score, bool) and 0 <= score <= 10
    return {
        "age_group": _one_of(raw.get("age_group"), AGE_GROUPS),
        "gender": _one_of(raw.get("gender"), ("male", "female")),
        "city": _free_text(raw.get("city")),
        "occupation": _free_text(raw.get("occupation")),
        "interests": _strings(raw.get("interests"), MAX_INTERESTS),
        "current_projects": _strings(raw.get("current_projects"), MAX_PROJECTS),
        "bot_score": score if valid_score else None,
        "bot_signals": _strings(raw.get("bot_signals"), 3),
        "sensitive": [k for k in SENSITIVE_KEYS if k in _strings(raw.get("sensitive"))],
    }


def profile_prompt(view: str, lines: list[str]) -> str:
    sensitive = "; ".join(f"{key} ({topics})" for key, topics in zip(SENSITIVE_KEYS, config.EXCLUDED_TOPICS))
    return (
        f"You are an analyst at {VIEWS[view]}. Below is everything recorded about one anonymous visitor "
        f"(local time, oldest first).\n\n" + "\n".join(lines) + "\n\n"
        "Describe this visitor for ad targeting, in English, using only the evidence above. "
        "Write \"unknown\" when the evidence says nothing. Fields:\n"
        f"- age_group: one of {', '.join(AGE_GROUPS)}, unknown\n"
        "- gender: male, female or unknown\n"
        "- city: a city name, or unknown\n"
        "- occupation: a job title, or unknown\n"
        f"- interests: up to {MAX_INTERESTS} different lasting interests, each a short phrase naming a subject\n"
        f"- current_projects: up to {MAX_PROJECTS} things the visitor is planning or about to buy now; "
        "an empty list if none\n"
        "- bot_score: a whole number from 0 (clearly a person) to 10 (clearly an automated program)\n"
        "- bot_signals: up to 3 short reasons for the bot_score\n"
        f"- sensitive: the keys of these categories the visitor seems interested in, an empty list if none: "
        f"{sensitive}\n"
        "Answer with one JSON object that has exactly these fields."
    )


def judge_prompt(truth: list[str], guess: str) -> str:
    return (
        "Real interests of a person:\n" + "\n".join(f"{i}. {t}" for i, t in enumerate(truth)) + "\n\n"
        f'Someone who watched their browsing guessed this interest: "{guess}"\n'
        "Which real interest is it clearly about (the same subject, possibly broader or narrower)? "
        "If none clearly matches, or you are unsure, answer null. "
        'Answer with a JSON object {"match": number or null}'
    )


@dataclass
class ViewResult:
    view: str
    lines: int
    profile: dict = field(default_factory=dict)
    guessed: list[str] = field(default_factory=list)  # interests + current projects
    matches: list[int | None] = field(default_factory=list)  # index into truth per guessed item

    @property
    def precision(self) -> float | None:
        return sum(m is not None for m in self.matches) / len(self.matches) if self.matches else None


@dataclass
class TrackerResult:
    persona_id: str
    end: str
    days: int
    truth: list[str]  # current interests first, then past projects
    current: int  # how many of `truth` are current interests
    signals: dict
    views: list[ViewResult]

    def recall(self, view: ViewResult) -> float | None:
        if not self.current:
            return None
        found = {m for m in view.matches if m is not None and m < self.current}
        return len(found) / self.current

    def to_dict(self) -> dict:
        data = asdict(self)
        for item, view in zip(data["views"], self.views):
            item.update(precision=view.precision, recall=self.recall(view))
        return data


def _judge(llm_fn: LLM, truth: list[str], guess: str) -> int | None:
    """One call per guess: a small model mixes up the pairs when asked about a whole list."""
    value = _ask_json(llm_fn, judge_prompt(truth, guess)).get("match")
    valid = isinstance(value, int) and not isinstance(value, bool) and 0 <= value < len(truth)
    return value if valid else None


def evaluate(persona: Persona, events: list[dict], end: date, days: int, llm_fn: LLM = llm.generate) -> TrackerResult:
    truth = [i.topic for i in persona.interests] + list(persona.past_projects)
    result = TrackerResult(persona.id, end.isoformat(), days, truth, len(persona.interests), bot_signals(events), [])
    for view in VIEWS:
        lines = build_view(events, view)
        item = ViewResult(view, len(lines))
        if lines:
            item.profile = clean_profile(_ask_json(llm_fn, profile_prompt(view, lines)))
            item.guessed = item.profile["interests"] + item.profile["current_projects"]
            item.matches = [_judge(llm_fn, truth, guess) for guess in item.guessed]
        result.views.append(item)
    return result


def save(result: TrackerResult, directory: Path | None = None) -> Path:
    path = (directory or config.DATA_DIR / "eval" / "tracker") / result.end / f"{result.persona_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value:.0%}"


def format_report(result: TrackerResult, persona: Persona) -> str:
    lines = [f"{persona.name}: {result.days} day(s) up to {result.end}",
             f"  real: {persona.age_group}, {persona.city}, {persona.occupation}",
             "  real interests: " + "; ".join(result.truth[:result.current])]
    if result.truth[result.current:]:
        lines.append("  past projects: " + "; ".join(result.truth[result.current:]))
    lines.append("  measured: " + ", ".join(f"{k} {v}" for k, v in result.signals.items()))
    for view in result.views:
        lines.append(f"  [{view.view} view, {view.lines} lines]")
        if not view.lines:
            lines.append("    nothing recorded")
            continue
        p = view.profile
        lines.append(f"    guessed: {p['age_group']}, {p['gender']}, {p['city']}, {p['occupation']}")
        for guess, match in zip(view.guessed, view.matches):
            lines.append(f"    {'✓' if match is not None else '✗'} {guess}"
                         + (f"  →  {result.truth[match]}" if match is not None else ""))
        lines.append(f"    found {_pct(result.recall(view))} of the real interests; "
                     f"{_pct(view.precision)} of the guesses are right")
        lines.append(f"    bot score {p['bot_score']}/10: " + "; ".join(p["bot_signals"]))
        flagged = p["sensitive"]
        lines.append("    sensitive: " + ("; ".join(flagged) + "  <- check!" if flagged else "none"))
    return "\n".join(lines)
