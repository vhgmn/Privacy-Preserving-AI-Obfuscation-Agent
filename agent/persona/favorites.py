"""Favourite sites: the persona's own reading decides which sites it keeps coming back to.

No list of sites is shipped: a site becomes a favourite only after the persona has read
there at length, by chance, and favourites are slowly forgotten again, as in real life.
"""
from __future__ import annotations

import dataclasses
import random
from datetime import date, datetime, timezone
from urllib.parse import urlsplit

from agent.browser import human
from agent.persona.schema import MAX_FAVORITES, Favorite, Persona

LOOKBACK_DAYS = 14
MIN_READ_SECONDS = 60  # a page read this long, or a site read on into, makes the site a candidate
ADOPT_CHANCE = 0.3  # per candidate and day
FORGET_CHANCE = 0.03  # per favourite and day
_NOT_FAVORITES = ("duckduckgo.com", "youtube.com")  # search and video have their own steps


def candidates(events: list[dict]) -> dict[str, str]:
    """Sites read at length (or read on into) in these log events: {site: home page}."""
    found: dict[str, str] = {}
    for event in events:
        kind, url = event.get("event"), event.get("url", "")
        long_read = kind == "read" and (event.get("seconds") or 0) >= MIN_READ_SECONDS
        if not (long_read or kind == "browse") or not url.startswith(("http://", "https://")):
            continue
        parts = urlsplit(url)
        site = (parts.hostname or "").lower().removeprefix("www.")
        if site and not site.endswith(_NOT_FAVORITES) and human.can_browse(url):
            found.setdefault(site, f"{parts.scheme}://{parts.netloc}/")
    return found


def update(persona: Persona, events: list[dict], rng: random.Random, now: datetime | None = None) -> Persona | None:
    """Forgets and adopts favourites for one day. Returns the changed persona, or None if nothing changed."""
    now = now or datetime.now(timezone.utc)
    kept = [f for f in persona.favorites if rng.random() >= FORGET_CHANCE]
    have = {f.site for f in kept}
    for site, url in sorted(candidates(events).items()):
        if len(kept) < MAX_FAVORITES and site not in have and rng.random() < ADOPT_CHANCE:
            kept.append(Favorite(site=site, url=url, since=now))
            have.add(site)
    if kept == persona.favorites:
        return None
    return dataclasses.replace(persona, favorites=kept)


def update_from_logs(persona: Persona, today: date, rng: random.Random | None = None) -> Persona | None:
    from agent.browser.runner import load_events

    return update(persona, load_events(persona.id, today, LOOKBACK_DAYS), rng or random.SystemRandom())
