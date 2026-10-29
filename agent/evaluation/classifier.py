"""Real-vs-decoy classifier: can a network observer (ISP, Wi-Fi owner) tell the user's browsing apart?

Such an observer sees which domains are visited and when, from every profile alike. The
user's side comes from their own Chrome history: only the time and the domain of each
visit are read, from a temporary copy that is deleted at once, and nothing about them is
written to disk; only the aggregate result is saved.
"""
from __future__ import annotations

import json
import math
import os
import shutil
import sqlite3
import statistics
import tempfile
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from agent import config

SESSION_GAP = timedelta(minutes=30)  # a longer pause starts a new session
SAME_PAGE = timedelta(seconds=2)  # repeated visits to one domain this close are one page load
SEARCH_DOMAINS = ("duckduckgo.com", "google.com", "bing.com", "yandex.ru", "yandex.com")
MIN_SESSIONS_PER_SIDE = 5
_CHROME_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)
_SUBFRAMES = (3, 4)  # page transition core types AUTO_SUBFRAME, MANUAL_SUBFRAME: not something a person opened

Visit = tuple[datetime, str]  # local time, domain


def chrome_history_path() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data" / "Default" / "History"


def domain(url: str) -> str | None:
    host = (urlsplit(url).hostname or "").removeprefix("www.")
    return host or None


def read_history(start: datetime, end: datetime, path: Path | None = None) -> list[Visit]:
    """Time and domain of every page the user opened between start and end (aware datetimes).

    Chrome keeps the file locked, so a copy is read and deleted right away. Full URLs never
    leave this function.
    """
    source = path or chrome_history_path()
    lo = int((start - _CHROME_EPOCH).total_seconds() * 1_000_000)
    hi = int((end - _CHROME_EPOCH).total_seconds() * 1_000_000)
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "History"
        shutil.copyfile(source, copy)
        connection = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
        try:
            rows = connection.execute(
                "SELECT visits.visit_time, visits.transition, urls.url FROM visits JOIN urls ON urls.id = visits.url "
                "WHERE visits.visit_time BETWEEN ? AND ? ORDER BY visits.visit_time", (lo, hi)).fetchall()
        finally:
            connection.close()
    visits = []
    for visit_time, transition, url in rows:
        name = domain(url) if url.startswith(("http://", "https://")) else None
        if name and (transition & 0xFF) not in _SUBFRAMES:
            visits.append(((_CHROME_EPOCH + timedelta(microseconds=visit_time)).astimezone(), name))
    return visits


def decoy_visits(events: list[dict]) -> list[Visit]:
    """The page loads a persona's session caused, as the network sees them."""
    visits = []
    for event in events:
        kind = event.get("event")
        when = datetime.fromisoformat(event["time"]).astimezone()
        if kind == "search":
            visits.append((when, "duckduckgo.com"))
        elif kind in ("open_result", "browse", "visit") and domain(event.get("url", "")):
            visits.append((when, domain(event["url"])))
        elif kind == "youtube":
            visits.append((when, "youtube.com"))
    return visits


def sessions(visits: list[Visit]) -> list[list[Visit]]:
    result: list[list[Visit]] = []
    for visit in sorted(visits):
        if result and visit[0] - result[-1][-1][0] <= SESSION_GAP:
            last = result[-1][-1]
            if not (visit[1] == last[1] and visit[0] - last[0] <= SAME_PAGE):
                result[-1].append(visit)
        else:
            result.append([visit])
    return result


FEATURES = ("hour_sin", "hour_cos", "weekend", "minutes", "pages", "domains", "gap_median_s", "gap_variation",
            "search_share", "youtube_share", "new_domain_share")


def features(groups: list[list[Visit]]) -> list[list[float]]:
    """One row per session. `new_domain_share`: domains this source had not visited before in the
    window; people keep returning to the same sites."""
    rows, seen = [], set()
    for group in groups:
        start, names = group[0][0], [name for _, name in group]
        gaps = [(b[0] - a[0]).total_seconds() for a, b in zip(group, group[1:])]
        hour = start.hour + start.minute / 60
        distinct = set(names)
        rows.append([
            math.sin(2 * math.pi * hour / 24), math.cos(2 * math.pi * hour / 24),
            1.0 if start.weekday() >= 5 else 0.0,
            (group[-1][0] - start).total_seconds() / 60, len(group), len(distinct),
            statistics.median(gaps) if gaps else 0.0,
            statistics.pstdev(gaps) / statistics.mean(gaps) if len(gaps) > 1 and statistics.mean(gaps) else 0.0,
            sum(n.endswith(SEARCH_DOMAINS) for n in names) / len(names),
            sum(n.endswith("youtube.com") for n in names) / len(names),
            len(distinct - seen) / len(distinct),
        ])
        seen |= distinct
    return rows


@dataclass
class Result:
    end: str
    days: int
    real_sessions: int
    decoy_sessions: int
    means: dict[str, list[float]]  # feature -> [real mean, decoy mean]
    auc: dict[str, float] | None = None  # model -> cross-validated AUC; 0.5 = cannot tell apart
    balanced_accuracy: dict[str, float] | None = None
    importance: dict[str, float] | None = None  # random forest, which features give decoys away
    note: str = ""


def evaluate(real: list[list[float]], decoy: list[list[float]], end: date, days: int) -> Result:
    def mean(rows, i):
        return round(statistics.mean(r[i] for r in rows), 3) if rows else float("nan")

    result = Result(end.isoformat(), days, len(real), len(decoy),
                    {name: [mean(real, i), mean(decoy, i)] for i, name in enumerate(FEATURES)})
    if min(len(real), len(decoy)) < MIN_SESSIONS_PER_SIDE:
        result.note = (f"too few sessions to train (need {MIN_SESSIONS_PER_SIDE} on each side); "
                       "only the averages are shown")
        return result

    from sklearn.ensemble import RandomForestClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import balanced_accuracy_score, roc_auc_score
    from sklearn.model_selection import StratifiedKFold, cross_val_predict
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    x = real + decoy
    y = [0] * len(real) + [1] * len(decoy)
    folds = StratifiedKFold(n_splits=min(5, len(real), len(decoy)), shuffle=True, random_state=0)
    models = {
        "logistic regression": make_pipeline(StandardScaler(), LogisticRegression(class_weight="balanced",
                                                                                  max_iter=1000)),
        "random forest": RandomForestClassifier(n_estimators=300, class_weight="balanced", random_state=0),
    }
    result.auc, result.balanced_accuracy = {}, {}
    for name, model in models.items():
        scores = cross_val_predict(model, x, y, cv=folds, method="predict_proba")[:, 1]
        result.auc[name] = round(float(roc_auc_score(y, scores)), 3)
        result.balanced_accuracy[name] = round(float(balanced_accuracy_score(y, [s >= 0.5 for s in scores])), 3)
    forest = models["random forest"].fit(x, y)
    ranked = sorted(zip(FEATURES, forest.feature_importances_), key=lambda item: -item[1])
    result.importance = {name: round(float(value), 3) for name, value in ranked}
    return result


def save(result: Result, directory: Path | None = None) -> Path:
    path = (directory or config.DATA_DIR / "eval" / "classifier") / f"{result.end}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(result), indent=2), encoding="utf-8")
    return path


def format_report(result: Result) -> str:
    lines = [f"Sessions in {result.days} day(s) up to {result.end}: yours {result.real_sessions}, "
             f"decoys {result.decoy_sessions}", "",
             f"{'feature':18} {'yours':>9} {'decoys':>9}"]
    for name, (real, decoy) in result.means.items():
        lines.append(f"{name:18} {real:>9} {decoy:>9}")
    lines.append("")
    if result.note:
        lines.append(f"Note: {result.note}.")
        return "\n".join(lines)
    for name, auc in result.auc.items():
        lines.append(f"{name}: AUC {auc:.2f}, balanced accuracy {result.balanced_accuracy[name]:.2f}")
    lines.append("(AUC 0.50 = the observer cannot tell your sessions from the decoys; 1.00 = always can)")
    top = list(result.importance.items())[:4]
    lines.append("What gives the decoys away most: " + ", ".join(f"{n} ({v:.2f})" for n, v in top))
    return "\n".join(lines)
