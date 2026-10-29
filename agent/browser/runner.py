"""Runs one planned session in the persona's own Chrome profile and logs what happened."""
from __future__ import annotations

import json
import random
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterator

from playwright.sync_api import BrowserContext
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from agent import config
from agent.browser import human, sites
from agent.persona.schema import Persona
from agent.planner.schema import Session

CHROME_ARGS = [
    "--disable-blink-features=AutomationControlled",  # otherwise navigator.webdriver is true
    "--mute-audio",  # videos play silently
]


def _logs_dir() -> Path:
    return config.DATA_DIR / "logs"


class SessionLog:
    """Appends one JSON line per event to data/logs/<date>/<persona_id>.jsonl (kept local, for evaluation)."""

    def __init__(self, persona_id: str, day: date, session_number: int, directory: Path | None = None):
        self.path = (directory or _logs_dir()) / day.isoformat() / f"{persona_id}.jsonl"
        self.session_number = session_number

    def write(self, event: str, **fields) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        record = {"time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                  "session": self.session_number, "event": event, **fields}
        with self.path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")


def match_user_languages(profile_dir: Path, languages: str | None) -> bool:
    """Gives the persona's Chrome the user's language list (navigator.languages, Accept-Language).

    All profiles share one device fingerprint, so a different language list would be the
    one thing that tells the user apart from the decoys. Returns True if it changed anything.
    """
    if not languages:
        return False
    path = profile_dir / "Default" / "Preferences"
    try:
        prefs = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        prefs = {}
    intl = prefs.setdefault("intl", {})
    if intl.get("selected_languages") == languages and intl.get("accept_languages") == languages:
        return False
    intl["selected_languages"] = intl["accept_languages"] = languages  # newer and older Chrome
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(prefs, ensure_ascii=False), encoding="utf-8")
    return True


def _user_languages() -> str | None:
    from agent.evaluation.fingerprint import latest_mine

    mine = latest_mine()
    return mine.get("languages") if mine else None


@contextmanager
def persona_browser(persona: Persona, on_own_desktop: bool = False, echo=print) -> Iterator[BrowserContext]:
    """Real Chrome (never headless) with a persistent profile that belongs to this persona only.

    With `on_own_desktop`, the window goes to a separate Windows virtual desktop so it does
    not get in the user's way.
    """
    args = list(CHROME_ARGS)
    before: set[int] = set()
    if on_own_desktop:
        from agent.scheduler import desktop

        args += desktop.OFFSCREEN_ARGS + desktop.KEEP_RUNNING_ARGS
        before = desktop.chrome_windows()
    profile_dir = config.PROFILES_DIR / persona.id
    match_user_languages(profile_dir, _user_languages())
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            channel="chrome",
            headless=False,
            args=args,
            ignore_default_args=["--enable-automation"],
            accept_downloads=False,
            no_viewport=True,  # use the real window size, like a person's browser
        )
        try:
            if on_own_desktop:
                echo(f"  window: {desktop.hide_new_windows(before)}")
            yield context
        finally:
            context.close()


def webdriver_flag(context: BrowserContext) -> object:
    page = context.pages[0] if context.pages else context.new_page()
    return page.evaluate("navigator.webdriver")


def _title(page) -> str | None:
    """The page title, for the evaluation's tracker view (a URL alone often says little)."""
    try:
        return page.title()[:200] or None
    except PlaywrightError:
        return None


def _browse_site(page, mouse, persona: Persona, visited: set[str], log: SessionLog,
                 rng: random.Random, echo) -> None:
    """Follows a few links inside the site; stops quietly when there is nothing good to follow."""
    behavior = persona.behavior
    if not human.can_browse(page.url):
        return
    for _ in range(human.browse_depth(behavior.patience, rng)):
        try:
            url = sites.follow_internal_link(page, mouse, visited, rng)
        except PlaywrightError as exc:
            log.write("browse_stopped", reason=str(exc).splitlines()[0][:200])
            return
        echo(f"  browse: {url}")
        log.write("browse", url=url)
        seconds = sites.read_page(page, behavior.reading_wpm, behavior.patience, rng)
        log.write("read", url=page.url, title=_title(page), seconds=round(seconds))


def run_session(context: BrowserContext, persona: Persona, session: Session, log: SessionLog,
                rng: random.Random | None = None, echo=print, browse: bool = True) -> str:
    """Executes the steps in order. Returns "done", "blocked" or "failed".

    A failed step (slow site, missing result) skips ahead to the next search, as a
    person would; a bot check stops the whole session. With `browse`, a read page may
    lead to a few more articles on the same site, as reading usually does.
    """
    rng = rng or random.SystemRandom()
    page = context.pages[0] if context.pages else context.new_page()
    mouse = sites.Mouse(page, rng)
    behavior = persona.behavior
    log.write("session_start", topic=session.topic, language=session.language,
              webdriver=page.evaluate("navigator.webdriver"))
    skip_to_next_search = False
    failures = 0
    visited: set[str] = set()  # a person does not reread the page they just read
    results_url = None
    try:
        for step in session.steps:
            if skip_to_next_search and step.type not in ("search", "youtube"):
                continue
            skip_to_next_search = False
            try:
                if step.type == "search":
                    echo(f"  search: {step.query}")
                    sites.ddg_search(page, mouse, step.query, behavior.typo_rate, rng)
                    log.write("search", query=step.query)
                elif step.type == "open_result":
                    results_url = page.url
                    url = sites.open_result(page, mouse, step.rank, visited)
                    echo(f"  open #{step.rank}: {url}")
                    log.write("open_result", rank=step.rank, url=url)
                elif step.type == "read":
                    seconds = sites.read_page(page, behavior.reading_wpm, behavior.patience, rng)
                    log.write("read", url=page.url, title=_title(page), seconds=round(seconds))
                    if browse:
                        _browse_site(page, mouse, persona, visited, log, rng, echo)
                    sites.back_to_results(page, results_url)
                elif step.type == "youtube":
                    echo(f"  youtube: {step.query}")
                    url, seconds = sites.youtube(page, mouse, step.query, behavior.typo_rate,
                                                 behavior.youtube_affinity, rng)
                    log.write("youtube", query=step.query, url=url, seconds=round(seconds))
            except PlaywrightError as exc:
                failures += 1
                message = str(exc).splitlines()[0][:200]
                echo(f"  step failed ({step.type}): {message}")
                log.write("step_failed", step=step.type, error=message)
                skip_to_next_search = True
    except sites.Blocked as exc:
        echo(f"  STOPPED: {exc}. Not trying to get past it.")
        log.write("blocked", url=page.url, reason=str(exc))
        return "blocked"
    outcome = "failed" if failures and failures >= len([s for s in session.steps if s.type == "search"]) else "done"
    log.write("session_end", outcome=outcome, failures=failures)
    return outcome
