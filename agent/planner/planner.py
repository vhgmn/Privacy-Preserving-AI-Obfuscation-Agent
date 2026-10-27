from __future__ import annotations

import random
from collections import Counter
from datetime import date, datetime, timezone
from typing import Callable

from agent import llm, translate
from agent.persona.generator import LLM, GenerationError, _ask_json, _is_sensitive, _retry, _stored_context
from agent.persona.schema import Interest, Persona
from agent.planner.schema import MAX_SESSIONS, MIN_SESSIONS, DailyPlan, Session, Step, check_query

Translate = Callable[[list[str], str], list[str]]  # (english_texts, target_language) -> translations

CREATIVE = {"temperature": 0.8}
PROJECT_BOOST = 2.0  # someone choosing a fridge searches for it nearly every day
MAX_PROJECT_SESSIONS_PER_DAY = 2
PAST_PROJECT_CHANCE = 0.1  # e.g. looking up the warranty of the fridge bought last month
LANGUAGE_NAMES = {"en": "English", "ru": "Russian"}
STYLE_HINTS = {
    "keywords": "short keyword fragments, no question words",
    "questions": "full natural questions",
    "mixed": "some keyword fragments and some questions",
}


def _write_queries(llm_fn: LLM, translate_fn: Translate | None, persona: Persona, topic: str,
                   description: str, language: str, count: int, youtube: bool) -> list[str]:
    # Armenian is written in English and translated: the small LLM cannot write Armenian.
    write_in = "en" if language == "hy" else language
    if youtube:
        task = f"Write {count} YouTube search query they would type to find a video about it."
    else:
        task = (f"Write {count} web search queries they would type in one sitting: the first starts the search, "
                f"each next one refines or follows up on what they probably found.")
    prompt = f"""Person: {_stored_context(persona)}
This person is now looking for information about: {topic}{f' ({description})' if description else ''}.
{task}
Write them in {LANGUAGE_NAMES[write_in]}, {STYLE_HINTS[persona.behavior.query_style]}, 1-6 words each.
Mention a country or city only when it matters, e.g. for prices, shops or places.
Return only a JSON object: {{"queries": [<{count} real queries as strings>]}}"""
    queries = _ask_json(llm_fn, prompt, CREATIVE).get("queries")
    if not isinstance(queries, list):
        raise ValueError("queries: expected a list")
    queries = [q.strip() for q in queries if isinstance(q, str) and q.strip()][:count]
    if len(queries) < count:
        raise ValueError(f"queries: expected {count}, got {len(queries)}")
    # Checked before translation: the safety check reads English, not Armenian.
    if _is_sensitive(llm_fn, {"topic": topic, "description": "; ".join(queries)}):
        raise ValueError(f"queries: flagged as sensitive: {queries}")
    if language == "hy":
        queries = [q.strip() for q in translate_fn(queries, "hy")]
    for query in queries:
        check_query(query, language)
    if len({q.casefold() for q in queries}) < len(queries):
        raise ValueError(f"queries: duplicates in {queries}")
    return queries


def _choose_topic(persona: Persona, rng: random.Random, now: datetime, used: Counter) -> tuple[str, str, str]:
    """Returns (topic, description, source)."""
    if persona.past_projects and rng.random() < PAST_PROJECT_CHANCE:
        return rng.choice(persona.past_projects), "", "past_project"
    active = [i for i in persona.interests if i.kind == "long_term" or i.expires_at > now]
    # A long-term interest comes up once a day; a project up to twice.
    fresh = [i for i in active if used[i.topic] < (MAX_PROJECT_SESSIONS_PER_DAY if i.kind == "project" else 1)]
    if not fresh:  # few interests and many sessions: spread the extra ones evenly
        fewest = min(used[i.topic] for i in active)
        fresh = [i for i in active if used[i.topic] == fewest]
    weights = [i.weight * (PROJECT_BOOST if i.kind == "project" else 1.0) for i in fresh]
    interest: Interest = rng.choices(fresh, weights=weights)[0]
    return interest.topic, interest.description, interest.kind


def _choose_language(persona: Persona, rng: random.Random, armenian_ok: bool) -> str:
    shares = {lang: share for lang, share in persona.languages.items() if armenian_ok or lang != "hy"}
    if not shares or sum(shares.values()) == 0:
        return "en"
    return rng.choices(list(shares), weights=list(shares.values()))[0]


def _result_rank(rng: random.Random, patience: float) -> int:
    # Impatient people click the first results; patient ones scroll further down.
    return 1 + min(4, int(rng.expovariate(1.0 / (0.3 + 1.5 * patience))))


def _session(llm_fn: LLM, translate_fn: Translate | None, persona: Persona, rng: random.Random,
             now: datetime, used: Counter, armenian_ok: bool) -> Session:
    topic, description, source = _choose_topic(persona, rng, now, used)
    language = _choose_language(persona, rng, armenian_ok)
    patience = persona.behavior.patience
    count = 2 + round(patience * 2)  # 2..4 searches

    queries = _retry("queries", lambda: _write_queries(
        llm_fn, translate_fn, persona, topic, description, language, count, youtube=False))
    steps = []
    for index, query in enumerate(queries):
        steps.append(Step("search", query, replaceable=index > 0))
        visits = 2 if rng.random() < patience * 0.5 else 1
        ranks: list[int] = []
        for _ in range(visits):
            rank = _result_rank(rng, patience)
            while rank in ranks:
                rank = rank % 5 + 1
            ranks.append(rank)
            steps += [Step("open_result", rank=rank), Step("read")]

    if rng.random() < persona.behavior.youtube_affinity * 0.6:
        try:
            video = _retry("youtube", lambda: _write_queries(
                llm_fn, translate_fn, persona, topic, description, language, 1, youtube=True))
            steps.append(Step("youtube", video[0]))
        except GenerationError:
            pass  # a session without a video is still a good session

    used[topic] += 1
    return Session(topic=topic, source=source, language=language, engine="duckduckgo", steps=steps)


def plan_day(persona: Persona, day: date, llm_fn: LLM = llm.generate, translate_fn: Translate | None = None,
             rng: random.Random | None = None, now: datetime | None = None) -> tuple[DailyPlan, list[str]]:
    """Plans one day for one persona. Returns the plan and human-readable warnings. Does not save."""
    rng = rng or random.SystemRandom()
    now = now or datetime.now(timezone.utc)
    warnings = []
    if translate_fn is None and translate.is_available():
        translate_fn = translate.translate
    armenian_ok = translate_fn is not None
    if not armenian_ok and persona.languages.get("hy"):
        warnings.append("Armenian translator not installed: Armenian sessions replaced by Russian/English")

    target = rng.randint(MIN_SESSIONS, MAX_SESSIONS)
    sessions, used = [], Counter()
    for _ in range(target):
        try:
            sessions.append(_session(llm_fn, translate_fn, persona, rng, now, used, armenian_ok))
        except GenerationError as exc:
            warnings.append(f"skipped a session: {exc}")
    if len(sessions) < MIN_SESSIONS:
        raise GenerationError(f"only {len(sessions)} of {target} sessions could be planned: {warnings}")
    try:
        return DailyPlan(persona_id=persona.id, date=day, created_at=now, sessions=sessions), warnings
    except ValueError as exc:
        raise GenerationError(f"plan: {exc}") from exc
