"""Real browser actions on DuckDuckGo, result pages and YouTube (Playwright, sync API)."""
from __future__ import annotations

import random
import time
from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page

from agent.browser import human

DDG_HOME = "https://duckduckgo.com/"
YOUTUBE_HOME = "https://www.youtube.com/"
LOAD_TIMEOUT_MS = 20_000
# DuckDuckGo: a textarea on the home page, an input on result pages.
DDG_SEARCH_BOX = 'textarea[name="q"]:visible, input[name="q"]:visible'
DDG_RESULT_LINK = 'article[data-testid="result"] a[data-testid="result-title-a"]'
YOUTUBE_SEARCH_BOX = 'input[name="search_query"]:visible'
# Any video link outside an ad container; YouTube changes its markup often, so stay generic.
_YOUTUBE_VIDEO_LINKS_JS = """() => [...document.querySelectorAll('a[href^="/watch?v="]')]
    .filter(a => !a.closest('ytd-ad-slot-renderer, ytd-in-feed-ad-layout-renderer, ytd-promoted-sparkles-web-renderer'))
    .filter(a => a.offsetWidth > 0 && a.offsetHeight > 0)
    .map(a => a.getAttribute('href'))"""


class Blocked(RuntimeError):
    """The site showed a CAPTCHA or bot check. The agent stops; it never tries to get past one."""


class Mouse:
    """Remembers where the pointer is, so every move starts from the last position."""

    def __init__(self, page: Page, rng: random.Random):
        self.page, self.rng = page, rng
        self.x, self.y = rng.uniform(200, 600), rng.uniform(150, 400)

    def move_to(self, locator: Locator) -> None:
        box = locator.bounding_box()
        if not box:
            return
        target = (box["x"] + box["width"] * self.rng.uniform(0.3, 0.7),
                  box["y"] + box["height"] * self.rng.uniform(0.35, 0.65))
        for x, y in human.mouse_path((self.x, self.y), target, self.rng):
            self.page.mouse.move(x, y)
            time.sleep(self.rng.uniform(0.005, 0.02))
        self.x, self.y = target

    def click(self, locator: Locator) -> None:
        locator.scroll_into_view_if_needed(timeout=LOAD_TIMEOUT_MS)
        self.move_to(locator)
        time.sleep(self.rng.uniform(0.08, 0.35))
        self.page.mouse.click(self.x, self.y)


def _page_text(page: Page, limit: int = 3000) -> str:
    try:
        return page.title() + " " + page.locator("body").inner_text(timeout=5_000)[:limit]
    except PlaywrightError:
        return ""


def check_not_blocked(page: Page) -> None:
    if human.is_blocked(_page_text(page)):
        raise Blocked(f"bot check on {urlparse(page.url).netloc}")


def wait_until(page: Page, ready, what: str, timeout_s: float = LOAD_TIMEOUT_MS / 1000) -> None:
    """Polls `ready()` from Python. Bot checks often appear a moment after the page loads,
    so they are looked for on every poll; on timeout the error says what the page showed."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            if ready():
                return
        except PlaywrightError:
            pass  # page still navigating
        check_not_blocked(page)
        time.sleep(0.5)
    shown = " ".join(_page_text(page, 160).split())
    raise PlaywrightError(f"timed out waiting for {what} on {urlparse(page.url).netloc}; page shows: {shown!r}")


def dismiss_cookie_banner(page: Page, mouse: Mouse) -> bool:
    """Clicks a visible 'reject' button if there is one. Never clicks 'accept'."""
    try:
        buttons = page.get_by_role("button").all()
    except PlaywrightError:
        return False
    for button in buttons[:60]:
        try:
            if button.is_visible() and human.is_reject_button(button.inner_text(timeout=500)):
                mouse.click(button)
                return True
        except PlaywrightError:
            continue
    return False


def type_like_human(page: Page, text: str, typo_rate: float, rng: random.Random) -> None:
    for action, char, delay in human.typing_actions(text, typo_rate, rng):
        time.sleep(delay)
        if action == "type":
            page.keyboard.insert_text(char)  # works for Armenian and Cyrillic regardless of keyboard layout
        elif action == "backspace":
            page.keyboard.press("Backspace")


def _search(page: Page, mouse: Mouse, box_selector: str, home: str, query: str,
            typo_rate: float, rng: random.Random) -> None:
    if not page.url.startswith(home):
        page.goto(home, wait_until="domcontentloaded", timeout=LOAD_TIMEOUT_MS)
        check_not_blocked(page)
        dismiss_cookie_banner(page, mouse)
    box = page.locator(box_selector).first
    wait_until(page, lambda: box.count() > 0 and box.is_visible(), "the search box")
    mouse.click(box)
    page.keyboard.press("Control+A")  # replace the previous query, as people do
    page.keyboard.press("Backspace")
    type_like_human(page, query, typo_rate, rng)
    time.sleep(rng.uniform(0.3, 1.2))
    page.keyboard.press("Enter")
    page.wait_for_load_state("domcontentloaded", timeout=LOAD_TIMEOUT_MS)
    check_not_blocked(page)


def ddg_search(page: Page, mouse: Mouse, query: str, typo_rate: float, rng: random.Random) -> None:
    before = page.url
    _search(page, mouse, DDG_SEARCH_BOX, DDG_HOME, query, typo_rate, rng)
    # The previous results stay on screen for a moment: wait until the address has changed too.
    links = page.locator(DDG_RESULT_LINK)
    wait_until(page, lambda: page.url != before and links.count() > 0, "search results")


def choose_result(hrefs: list[str], rank: int, visited: set[str]) -> int | None:
    """Index of the result to open: the rank-th one, or if it was already read in this
    session, the next unread one below it (then above it). None if all were read."""
    if not hrefs:
        return None
    wanted = min(rank, len(hrefs)) - 1
    below = range(wanted, len(hrefs))
    above = range(wanted - 1, -1, -1)
    return next((i for i in [*below, *above] if hrefs[i] not in visited), None)


def open_result(page: Page, mouse: Mouse, rank: int, visited: set[str]) -> str:
    """Opens an organic result (see choose_result) and adds it to `visited`. Returns its URL."""
    links = page.locator(DDG_RESULT_LINK)
    hrefs = [link.get_attribute("href") or "" for link in links.all()]
    index = choose_result(hrefs, rank, visited)
    if index is None:
        raise PlaywrightError("no unread results to open")
    link = links.nth(index)
    # Results open in the same tab, so 'back' returns to them for the next click.
    link.evaluate("a => a.removeAttribute('target')")
    mouse.click(link)
    page.wait_for_load_state("domcontentloaded", timeout=LOAD_TIMEOUT_MS)
    visited.update({hrefs[index], page.url})  # both: the link may redirect
    check_not_blocked(page)
    dismiss_cookie_banner(page, mouse)
    return page.url


def read_page(page: Page, reading_wpm: int, patience: float, rng: random.Random) -> float:
    try:
        words = len(page.locator("body").inner_text(timeout=5_000).split())
    except PlaywrightError:
        words = 300
    seconds = human.read_seconds(words, reading_wpm, patience, rng)
    _scroll_for(page, seconds, rng)
    return seconds


def _scroll_for(page: Page, seconds: float, rng: random.Random) -> None:
    started = time.monotonic()
    for pause, pixels in human.scroll_plan(seconds, rng):
        time.sleep(pause)
        for _ in range(rng.randint(2, 5)):  # a burst of wheel ticks
            page.mouse.wheel(0, pixels / 3)
            time.sleep(rng.uniform(0.03, 0.12))
    time.sleep(max(0.0, seconds - (time.monotonic() - started)))


def back_to_results(page: Page) -> None:
    page.go_back(wait_until="domcontentloaded", timeout=LOAD_TIMEOUT_MS)


def youtube(page: Page, mouse: Mouse, query: str, typo_rate: float, youtube_affinity: float,
            rng: random.Random) -> tuple[str, float]:
    """Searches YouTube, opens the first video that is not an ad, watches it (Chrome is muted)."""
    _search(page, mouse, YOUTUBE_SEARCH_BOX, YOUTUBE_HOME, query, typo_rate, rng)
    # Polled from Python: YouTube's Trusted Types policy forbids the string evaluation
    # that page.wait_for_function relies on.
    found: list[str] = []

    def videos_listed() -> bool:
        found[:] = page.evaluate(_YOUTUBE_VIDEO_LINKS_JS)
        return bool(found)

    wait_until(page, videos_listed, "video results")
    href = found[0]
    mouse.click(page.locator(f'a[href="{href}"]').first)
    page.wait_for_load_state("domcontentloaded", timeout=LOAD_TIMEOUT_MS)
    check_not_blocked(page)
    seconds = human.watch_seconds(youtube_affinity, rng)
    _scroll_for(page, seconds, rng)
    return page.url, seconds
