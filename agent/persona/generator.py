from __future__ import annotations

import dataclasses
import json
import random
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable

from agent import config, llm
from agent.persona.names import FEMALE_NAMES, MALE_NAMES
from agent.persona.schema import AGE_GROUPS, MAX_PAST_PROJECTS, QUERY_STYLES, Behavior, Interest, Persona

LLM = Callable[..., str]  # same signature as llm.generate
MAX_ATTEMPTS = 3
CREATIVE = {"temperature": 0.9}
STRICT = {"temperature": 0}

# Rough share of adult internet users by age group; younger people are online more.
AGE_WEIGHTS = {"18-24": 0.22, "25-34": 0.28, "35-44": 0.22, "45-54": 0.16, "55+": 0.12}

KIND_DESCRIPTIONS = {
    "long_term": "a long-term hobby or interest",
    "project": "a short practical project lasting a few weeks, such as choosing a product to buy or planning a trip",
}


class GenerationError(RuntimeError):
    pass


def _is_text(value) -> bool:
    return isinstance(value, str) and value.strip() != ""


def _key(topic: str) -> str:
    return topic.strip().casefold()


def _ask_json(llm_fn: LLM, prompt: str, options: dict) -> dict:
    data = json.loads(llm_fn(prompt, json_mode=True, options=options))
    if not isinstance(data, dict):
        raise ValueError("expected a JSON object")
    return data


def _retry(step: str, fn: Callable[[], dict | list]):
    last_error = None
    for _ in range(MAX_ATTEMPTS):
        try:
            return fn()
        except ValueError as exc:  # includes json.JSONDecodeError
            last_error = exc
    raise GenerationError(f"{step}: gave up after {MAX_ATTEMPTS} attempts: {last_error}")


# --- numeric traits: sampled by code, small LLMs give every persona the same numbers ---

def _sample_languages(rng: random.Random) -> dict[str, float]:
    # Armenian dominates for most people in Armenia; Russian and English vary a lot.
    raw = {"hy": rng.uniform(0.4, 0.8), "ru": rng.uniform(0.05, 0.4), "en": rng.uniform(0.05, 0.3)}
    total = sum(raw.values())
    shares = {lang: round(value / total, 3) for lang, value in raw.items()}
    shares["hy"] = round(1 - shares["ru"] - shares["en"], 3)
    return shares


def _sample_behavior(rng: random.Random, query_style: str) -> Behavior:
    return Behavior(
        query_style=query_style,
        reading_wpm=int(min(400, max(150, rng.gauss(240, 50)))),
        typo_rate=round(min(0.06, rng.betavariate(1.5, 60)), 4),
        youtube_affinity=round(rng.betavariate(2, 2), 2),
        patience=round(rng.betavariate(2, 2), 2),
    )


def _sample_weights(rng: random.Random, n: int) -> list[float]:
    raw = [rng.uniform(0.5, 1.5) for _ in range(n)]
    total = sum(raw)
    return [round(value / total, 3) for value in raw]


# --- LLM steps ---

def _pick_name(rng: random.Random, gender: str, existing: list[Persona]) -> str:
    pool = MALE_NAMES if gender == "man" else FEMALE_NAMES
    taken = {p.name for p in existing}
    return rng.choice([n for n in pool if n not in taken] or list(pool))


def _identity(llm_fn: LLM, gender: str, age_group: str, city: str, taken_occupations: set[str]) -> dict:
    # The name is deliberately not given: the model mangles Armenian names when it
    # repeats them in Latin script.
    taken = ", ".join(sorted(taken_occupations)) or "none"
    prompt = f"""You are inventing a fictional, ordinary {gender} who lives in {city}, Armenia and is aged {age_group}.
Other invented people already have these occupations: {taken}. Choose a clearly different occupation.
Return only a JSON object with these keys:
"occupation": a short job title in English,
"query_style": how they type web searches, exactly one of "keywords", "questions", "mixed",
"bio": two sentences in English about their daily life, using he or she instead of a name."""
    data = _ask_json(llm_fn, prompt, CREATIVE)
    for key in ("occupation", "query_style", "bio"):
        if not _is_text(data.get(key)):
            raise ValueError(f"identity: missing {key!r}")
    if data["query_style"] not in QUERY_STYLES:
        raise ValueError(f"identity: bad query_style {data['query_style']!r}")
    if _key(data["occupation"]) in taken_occupations:
        raise ValueError(f"identity: occupation {data['occupation']!r} already taken")
    return data


def _person_context(identity: dict, gender: str, age_group: str, city: str) -> str:
    return f"A {gender}, {identity['occupation']}, aged {age_group}, living in {city}, Armenia. {identity['bio']}"


def _exclusions(taken_topics: set[str]) -> str:
    rules = f"Topics must NOT relate to any of: {'; '.join(config.EXCLUDED_TOPICS)}."
    if taken_topics:
        rules += f"\nTopics must be different from: {', '.join(sorted(taken_topics))}."
    return rules


def _interest_candidates(llm_fn: LLM, context: str, long_categories: list[str], project_categories: list[str],
                         taken_topics: set[str]) -> list[dict]:
    # Two separate lists: small models reliably split them this way on their own,
    # but often drop a per-item "kind" field.
    prompt = f"""Person: {context}
This person searches the web about their private life, mostly unrelated to their job.
For each of these areas, give one specific long-term interest ({KIND_DESCRIPTIONS['long_term']}) they have: {'; '.join(long_categories)}.
For each of these areas, give one specific short project they are working on now: {'; '.join(project_categories)}.
Be concrete: name a specific team, dish, author, place, product type or technique rather than a whole field.
{_exclusions(taken_topics)}
Return only a JSON object, items in the same order as the areas: {{"long_term": [{{"topic": "2-4 words", "description": "one short sentence"}}], "projects": [same shape]}}"""
    data = _ask_json(llm_fn, prompt, CREATIVE)
    result = []
    for key, kind, categories in (("long_term", "long_term", long_categories),
                                  ("projects", "project", project_categories)):
        items = data.get(key)
        if not isinstance(items, list):
            raise ValueError(f"interests: expected a list under {key!r}")
        valid = [i for i in items if isinstance(i, dict) and _is_text(i.get("topic")) and _is_text(i.get("description"))]
        # Matched by position; the category is only used to steer a replacement.
        # The model often returns one item too few: those slots stay empty (topic None)
        # and are filled by the replacement step instead of redoing the whole list.
        for index, category in enumerate(categories):
            item = valid[index] if index < len(valid) else {"topic": None, "description": None}
            result.append({"topic": item["topic"], "description": item["description"], "kind": kind, "category": category})
    if all(item["topic"] is None for item in result):
        raise ValueError("interests: no usable items")
    return result


def _is_sensitive(llm_fn: LLM, item: dict) -> bool:
    prompt = f"""Does this topic relate to any of these categories: {'; '.join(config.EXCLUDED_TOPICS)}?
Topic: {item['topic']}: {item['description']}
Return only a JSON object: {{"sensitive": true or false}}"""
    try:
        verdict = _ask_json(llm_fn, prompt, STRICT).get("sensitive")
    except ValueError:
        return True  # unclear answer: reject rather than risk it
    return verdict is not False


_CATEGORY_KEYS = {_key(c) for c in config.INTEREST_CATEGORIES + config.PROJECT_CATEGORIES}


def _accept(llm_fn: LLM, item: dict, taken_topics: set[str]) -> bool:
    key = _key(item["topic"])
    # A topic that just repeats an area name ("Cooking and Food") is too vague to plan searches around.
    return key not in taken_topics and key not in _CATEGORY_KEYS and not _is_sensitive(llm_fn, item)


def _replacement(llm_fn: LLM, context: str, kind: str, category: str, taken_topics: set[str]) -> dict:
    prompt = f"""Person: {context}
Suggest one more thing this person would search the web about: {KIND_DESCRIPTIONS[kind]}, in the area "{category}".
Be concrete: name a specific team, dish, author, place, product type or technique rather than a whole field.
{_exclusions(taken_topics)}
Return only a JSON object: {{"topic": "2-4 words", "description": "one short sentence"}}"""
    data = _ask_json(llm_fn, prompt, CREATIVE)
    if not (_is_text(data.get("topic")) and _is_text(data.get("description"))):
        raise ValueError("replacement: missing topic or description")
    item = {"topic": data["topic"], "description": data["description"], "kind": kind, "category": category}
    if not _accept(llm_fn, item, taken_topics):
        raise ValueError(f"replacement: rejected topic {item['topic']!r}")
    return item


def generate_persona(existing: Iterable[Persona] = (), llm_fn: LLM = llm.generate,
                     rng: random.Random | None = None, now: datetime | None = None) -> Persona:
    """Creates one persona that differs from `existing`. Does not save it."""
    existing = list(existing)
    rng = rng or random.SystemRandom()
    now = now or datetime.now(timezone.utc)

    age_group = rng.choices(AGE_GROUPS, weights=[AGE_WEIGHTS[a] for a in AGE_GROUPS])[0]
    city = rng.choices(list(config.CITIES), weights=list(config.CITIES.values()))[0]
    long_categories = rng.sample(config.INTEREST_CATEGORIES, rng.randint(2, 4))
    project_categories = rng.sample(config.PROJECT_CATEGORIES, rng.randint(1, 2))
    gender = rng.choice(("man", "woman"))  # only steers the LLM; not stored
    name = _pick_name(rng, gender, existing)

    taken_occupations = {_key(p.occupation) for p in existing}
    identity = _retry("identity", lambda: _identity(llm_fn, gender, age_group, city, taken_occupations))
    context = _person_context(identity, gender, age_group, city)

    taken_topics = {_key(i.topic) for p in existing for i in p.interests}
    candidates = _retry("interests", lambda: _interest_candidates(
        llm_fn, context, long_categories, project_categories, taken_topics))
    accepted = []
    for item in candidates:
        if item["topic"] is None or not _accept(llm_fn, item, taken_topics):
            item = _retry(f"replacement {item['kind']}",
                          lambda kind=item["kind"], category=item["category"]:
                          _replacement(llm_fn, context, kind, category, taken_topics))
        taken_topics.add(_key(item["topic"]))
        accepted.append(item)

    interests = [
        Interest(
            topic=item["topic"].strip(),
            description=item["description"].strip(),
            kind=item["kind"],
            weight=weight,
            created_at=now,
            expires_at=now + timedelta(days=rng.randint(7, 42)) if item["kind"] == "project" else None,
        )
        for item, weight in zip(accepted, _sample_weights(rng, len(accepted)))
    ]

    taken_ids = {p.id for p in existing}
    persona_id = f"{rng.getrandbits(32):08x}"
    while persona_id in taken_ids:
        persona_id = f"{rng.getrandbits(32):08x}"

    try:
        return Persona(
            id=persona_id,
            name=name,
            age_group=age_group,
            occupation=identity["occupation"].strip(),
            city=city,
            languages=_sample_languages(rng),
            interests=interests,
            behavior=_sample_behavior(rng, identity["query_style"]),
            created_at=now,
        )
    except ValueError as exc:
        raise GenerationError(f"assembly: {exc}") from exc


# --- rotation: projects end and new ones begin, like in a real person's life ---

def _stored_context(persona: Persona) -> str:
    # The bio and gender used at creation are not stored; the name list tells the
    # gender, and long-term interests describe the person at least as well as a bio.
    gender = "woman" if persona.name in FEMALE_NAMES else "man"
    hobbies = ", ".join(i.topic for i in persona.interests if i.kind == "long_term")
    context = (f"A {gender}, {persona.occupation}, aged {persona.age_group}, living in {persona.city}, Armenia. "
               f"Long-term interests: {hobbies}.")
    if persona.past_projects:
        context += f" Recently finished projects: {', '.join(persona.past_projects[-3:])}."
    return context


def refresh_persona(persona: Persona, others: Iterable[Persona] = (), llm_fn: LLM = llm.generate,
                    rng: random.Random | None = None, now: datetime | None = None) -> Persona | None:
    """Replaces each expired project with a new one.

    Returns the updated persona, or None when nothing has expired (no LLM calls then).
    Does not save; on failure raises GenerationError and the original stays untouched.
    """
    rng = rng or random.SystemRandom()
    now = now or datetime.now(timezone.utc)
    expired = [i for i in persona.interests if i.kind == "project" and i.expires_at <= now]
    if not expired:
        return None

    kept = [i for i in persona.interests if i not in expired]
    past_projects = (persona.past_projects + [i.topic for i in expired])[-MAX_PAST_PROJECTS:]
    context = _stored_context(dataclasses.replace(persona, past_projects=past_projects))
    taken_topics = ({_key(i.topic) for p in [persona, *others] for i in p.interests}
                    | {_key(t) for t in past_projects})

    # A new project gets a weight comparable to the interests already there.
    average = sum(i.weight for i in kept) / len(kept)
    new_projects = []
    for _ in expired:
        category = rng.choice(config.PROJECT_CATEGORIES)
        item = _retry("new project", lambda: _replacement(llm_fn, context, "project", category, taken_topics))
        taken_topics.add(_key(item["topic"]))
        new_projects.append(Interest(
            topic=item["topic"].strip(),
            description=item["description"].strip(),
            kind="project",
            weight=average * rng.uniform(0.5, 1.5),
            created_at=now,
            expires_at=now + timedelta(days=rng.randint(7, 42)),
        ))

    interests = kept + new_projects
    total = sum(i.weight for i in interests)
    interests = [dataclasses.replace(i, weight=round(i.weight / total, 3)) for i in interests]
    try:
        return dataclasses.replace(persona, interests=interests, past_projects=past_projects)
    except ValueError as exc:
        raise GenerationError(f"refresh: {exc}") from exc
