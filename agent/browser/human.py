"""Human-like timing and movement. Pure functions of a random generator, so they can be tested
without a browser; sites.py turns them into real keyboard, mouse and scroll actions."""
from __future__ import annotations

import math
import random
import re
from urllib.parse import urlsplit

MIN_READ_SECONDS, MAX_READ_SECONDS = 15, 300
USUAL_MAX_READ_SECONDS = 180  # long pages end somewhere between this and the maximum
MIN_WATCH_SECONDS, MAX_WATCH_SECONDS = 30, 180
MAX_BROWSE_DEPTH = 3  # links followed inside one site after reading a result
# Without an account these show a login wall; nobody browses deeper into them.
SOCIAL_SITES = ("facebook.com", "instagram.com", "x.com", "twitter.com", "tiktok.com", "vk.com", "ok.ru",
                "linkedin.com")

# Links a person reading an article would not follow, or that the agent must never use.
_SKIP_LINK = re.compile(
    r"login|log-in|signin|sign-in|signup|sign-up|register|account|profile|cart|basket|checkout|order|subscribe"
    r"|download|print|share|logout|password|contact|privacy|cookie|terms|wp-admin|feed|rss"
    r"|\.(pdf|zip|rar|exe|msi|apk|docx?|xlsx?|pptx?|jpe?g|png|gif|webp|mp3|mp4)(\?|$)",
    re.IGNORECASE,
)

# Pages that mean "a bot was noticed". The agent stops instead of trying to get past them.
_BLOCK_PATTERNS = re.compile(
    r"unusual traffic|verify you are human|bots use duckduckgo too|подтвердите, что вы не робот|необычный трафик",
    re.IGNORECASE,
)
# Words that also appear on ordinary pages (a contact form "protected by reCAPTCHA"), so they
# only count on a page with little else on it, as real bot-check pages are.
_WEAK_BLOCK_PATTERNS = re.compile(r"captcha|are you a robot|not a robot", re.IGNORECASE)
BLOCK_PAGE_MAX_CHARS = 1500
# Cookie banners: only ever click a button that refuses. Accepting is never automated.
_REJECT_BUTTON = re.compile(
    r"^\s*(reject all|reject|decline|decline all|refuse|only necessary|necessary only"
    r"|отклонить все|отклонить|отказаться|только необходимые|մերժել)\s*$",
    re.IGNORECASE,
)


def is_blocked(text: str) -> bool:
    text = text or ""
    if _BLOCK_PATTERNS.search(text):
        return True
    return len(" ".join(text.split())) <= BLOCK_PAGE_MAX_CHARS and bool(_WEAK_BLOCK_PATTERNS.search(text))


def is_reject_button(label: str) -> bool:
    return bool(_REJECT_BUTTON.match(label or ""))


def _lognormal(rng: random.Random, median: float, sigma: float, lo: float, hi: float) -> float:
    return min(hi, max(lo, rng.lognormvariate(math.log(median), sigma)))


def key_delay(rng: random.Random, char: str = "a") -> float:
    """Seconds before the next key. Spaces get a slightly longer pause, as between words."""
    return _lognormal(rng, 0.22 if char == " " else 0.14, 0.45, 0.04, 1.2)


def typing_actions(text: str, typo_rate: float, rng: random.Random) -> list[tuple[str, str, float]]:
    """Keystrokes for typing `text`: ("type", char, delay), ("backspace", "", delay) or ("pause", "", delay).

    A typo inserts a wrong letter taken from the same text (so it is in the same alphabet),
    the typist notices it after 0-2 more letters, deletes back and continues correctly.
    """
    letters = [c for c in text if c.isalpha()] or list(text)
    actions: list[tuple[str, str, float]] = []
    i = 0
    while i < len(text):
        char = text[i]
        if char.isalpha() and rng.random() < typo_rate:
            wrong = rng.choice([c for c in letters if c.casefold() != char.casefold()] or [char])
            noticed_after = min(rng.randint(0, 2), len(text) - i - 1)
            typed = [wrong] + list(text[i + 1:i + 1 + noticed_after])
            for c in typed:
                actions.append(("type", c, key_delay(rng, c)))
            actions.append(("pause", "", _lognormal(rng, 0.35, 0.4, 0.15, 1.5)))  # "oops"
            for _ in typed:
                actions.append(("backspace", "", key_delay(rng)))
        actions.append(("type", char, key_delay(rng, char)))
        i += 1
    return actions


def read_seconds(word_count: int, reading_wpm: int, patience: float, rng: random.Random) -> float:
    """How long a person stays on a page: part of the full reading time, more if patient."""
    full = word_count / reading_wpm * 60
    share = min(1.0, max(0.1, patience * rng.uniform(0.4, 1.2)))
    seconds = full * share * rng.uniform(0.85, 1.15)
    # No fixed limits: a run of reads lasting exactly the same seconds would give the code away.
    limit = rng.uniform(USUAL_MAX_READ_SECONDS, MAX_READ_SECONDS)
    if seconds > limit:
        seconds = limit * rng.uniform(0.75, 1.0)
    if seconds < 2 * MIN_READ_SECONDS:
        seconds = max(seconds, rng.uniform(MIN_READ_SECONDS, 2 * MIN_READ_SECONDS))  # even a glance takes a while
    return seconds


def browse_depth(patience: float, rng: random.Random) -> int:
    """How many links inside the site to follow after reading a page: often none or one,
    more for a patient reader."""
    depth = 0
    while depth < MAX_BROWSE_DEPTH and rng.random() < 0.3 + 0.4 * patience:
        depth += 1
    return depth


def _site(host: str) -> str:
    """Rough site name: the last two labels (news.example.am -> example.am)."""
    return ".".join(host.lower().removeprefix("www.").split(".")[-2:])


def page_key(url: str) -> str:
    """What makes two addresses the same page: host and path, without a trailing slash,
    query or fragment (facebook.com/shop/ and facebook.com/shop?ref=x are one page)."""
    parts = urlsplit(url)
    return (parts.hostname or "").lower().removeprefix("www.") + parts.path.rstrip("/")


def can_browse(url: str) -> bool:
    return _site(urlsplit(url).hostname or "") not in SOCIAL_SITES


def choose_internal_link(links: list[tuple[str, str]], current: str, visited: set[str],
                         rng: random.Random) -> int | None:
    """Index of a link (href, visible text) to another article on the same site, or None.

    Article links have a few words of text; menus, logins, files and other sites are skipped.
    """
    here = urlsplit(current)
    seen = {page_key(url) for url in visited} | {page_key(current)}
    candidates, weights = [], []
    for i, (href, text) in enumerate(links):
        target = urlsplit(href)
        words = len(text.split())
        if (target.scheme not in ("http", "https") or _site(target.hostname or "") != _site(here.hostname or "")
                or page_key(href) in seen or target.path in ("", "/") or _SKIP_LINK.search(href)
                or not 3 <= words <= 25):
            continue
        candidates.append(i)
        weights.append(min(words, 12))  # headline-like texts are more tempting than "more"
    return rng.choices(candidates, weights)[0] if candidates else None


def watch_seconds(youtube_affinity: float, rng: random.Random) -> float:
    base = MIN_WATCH_SECONDS + youtube_affinity * (MAX_WATCH_SECONDS - MIN_WATCH_SECONDS)
    return min(MAX_WATCH_SECONDS, max(MIN_WATCH_SECONDS, base * rng.uniform(0.6, 1.2)))


def scroll_plan(seconds: float, rng: random.Random) -> list[tuple[float, int]]:
    """(pause_before, pixels) bursts that fill `seconds`: mostly down, now and then back up."""
    plan, used = [], 0.0
    while used < seconds:
        pause = _lognormal(rng, 4.0, 0.6, 1.0, 15.0)
        if used + pause > seconds:
            break
        pixels = rng.randint(120, 520) * (-1 if rng.random() < 0.12 else 1)
        plan.append((pause, pixels))
        used += pause
    return plan


def mouse_path(start: tuple[float, float], end: tuple[float, float], rng: random.Random,
               steps: int | None = None) -> list[tuple[float, float]]:
    """Points along a curved (quadratic Bezier) path with a random bend, ending exactly at `end`."""
    (x0, y0), (x2, y2) = start, end
    distance = math.hypot(x2 - x0, y2 - y0)
    steps = steps or max(8, min(40, int(distance / 25)))
    bend = rng.uniform(-0.3, 0.3) * distance
    # control point: midpoint pushed sideways, perpendicular to the straight line
    nx, ny = (-(y2 - y0) / distance, (x2 - x0) / distance) if distance else (0.0, 0.0)
    cx, cy = (x0 + x2) / 2 + nx * bend, (y0 + y2) / 2 + ny * bend
    points = []
    for k in range(1, steps + 1):
        t = k / steps
        t = t * t * (3 - 2 * t)  # ease in and out
        x = (1 - t) ** 2 * x0 + 2 * (1 - t) * t * cx + t ** 2 * x2
        y = (1 - t) ** 2 * y0 + 2 * (1 - t) * t * cy + t ** 2 * y2
        points.append((x, y))
    return points
