"""Which other companies' servers each page made Chrome talk to (for the evaluation, phase 5d).

Only domain names are kept: for every third-party domain, the sites on which it was loaded.
A domain seen on several sites can follow the persona from one to the next.
"""
from __future__ import annotations

from urllib.parse import urlsplit

from agent.browser.human import _site


class ThirdParties:
    def __init__(self) -> None:
        self.top: str | None = None  # site of the page in the tab right now
        self.seen: dict[str, set[str]] = {}  # third-party site -> sites it was loaded on

    def record(self, url: str, is_page_load: bool) -> None:
        if not url.startswith(("http://", "https://")):
            return  # data:, blob:, chrome-extension: and the like
        site = _site(urlsplit(url).hostname or "")
        if not site:
            return
        if is_page_load:
            self.top = site
        elif self.top and site != self.top:
            self.seen.setdefault(site, set()).add(self.top)

    def summary(self) -> dict[str, list[str]]:
        return {domain: sorted(sites) for domain, sites in sorted(self.seen.items())}
