"""Linkability audit: how alike do the persona profiles and the user's own Chrome look to a site?

Everything is collected locally (a page Playwright serves itself, or one on 127.0.0.1), never on an
external fingerprinting site. Until traffic goes through Nym, every profile also shares
the user's IP, which this audit cannot change.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from datetime import date
from http.server import BaseHTTPRequestHandler, HTTPServer
from itertools import combinations
from pathlib import Path

from agent import config

# Fonts a site can probe by measuring text width. Mostly Windows/Office ones plus a
# few that only some machines have, since rare fonts are what make a fingerprint unique.
FONTS = (
    "Arial", "Arial Black", "Bahnschrift", "Calibri", "Cambria", "Candara", "Comic Sans MS", "Consolas",
    "Constantia", "Corbel", "Courier New", "Ebrima", "Franklin Gothic Medium", "Gabriola", "Gadugi",
    "Georgia", "Impact", "Ink Free", "Leelawadee UI", "Lucida Console", "Lucida Sans Unicode",
    "Malgun Gothic", "Microsoft YaHei", "MV Boli", "Nirmala UI", "Palatino Linotype", "Segoe Print",
    "Segoe Script", "Segoe UI", "SimSun", "Sitka Text", "Sylfaen", "Tahoma", "Times New Roman",
    "Trebuchet MS", "Verdana", "Yu Gothic", "Helvetica", "Roboto", "Open Sans", "Noto Sans",
    "DejaVu Sans", "Cascadia Code", "Fira Code", "JetBrains Mono", "Source Code Pro", "Myriad Pro",
)

# An async JS function; the canvas image comes back as a data URL and is hashed in
# Python, the same way for persona profiles and the user's Chrome.
PROBE_JS = """async () => {
  const n = navigator, fp = {};
  fp.userAgent = n.userAgent;
  fp.platform = n.platform;
  fp.languages = (n.languages || []).join(",");
  fp.timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  fp.timezoneOffset = new Date().getTimezoneOffset();
  fp.screen = `${screen.width}x${screen.height}`;
  fp.availScreen = `${screen.availWidth}x${screen.availHeight}`;
  fp.colorDepth = screen.colorDepth;
  fp.devicePixelRatio = window.devicePixelRatio;
  fp.hardwareConcurrency = n.hardwareConcurrency;
  fp.deviceMemory = n.deviceMemory ?? null;
  fp.maxTouchPoints = n.maxTouchPoints;
  fp.webdriver = n.webdriver;
  fp.pdfViewerEnabled = n.pdfViewerEnabled ?? null;
  fp.plugins = n.plugins ? n.plugins.length : 0;
  fp.doNotTrack = n.doNotTrack;
  if (n.userAgentData) {
    fp.uaBrands = n.userAgentData.brands.map(b => `${b.brand} ${b.version}`).join(", ");
    try {
      const h = await n.userAgentData.getHighEntropyValues(["platformVersion", "architecture", "fullVersionList"]);
      fp.uaPlatformVersion = h.platformVersion;
      fp.uaArchitecture = h.architecture;
      fp.uaFullVersions = (h.fullVersionList || []).map(b => `${b.brand} ${b.version}`).join(", ");
    } catch (e) {}
  }
  try {
    const gl = document.createElement("canvas").getContext("webgl");
    const ext = gl.getExtension("WEBGL_debug_renderer_info");
    fp.webglVendor = ext ? gl.getParameter(ext.UNMASKED_VENDOR_WEBGL) : gl.getParameter(gl.VENDOR);
    fp.webglRenderer = ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER);
  } catch (e) { fp.webglVendor = fp.webglRenderer = null; }
  const c = document.createElement("canvas");
  c.width = 300; c.height = 60;
  const x = c.getContext("2d");
  x.textBaseline = "top"; x.font = "16px Arial";
  x.fillStyle = "#f60"; x.fillRect(110, 1, 62, 20);
  x.fillStyle = "#069"; x.fillText("Fingerprint, Բարև, Привет \\u{1F603}", 2, 15);
  x.fillStyle = "rgba(102,204,0,0.7)"; x.fillText("Fingerprint, Բարև, Привет \\u{1F603}", 4, 17);
  fp.canvas = c.toDataURL();
  const body = document.body || document.documentElement.appendChild(document.createElement("body"));
  const span = document.createElement("span");
  span.style.cssText = "position:absolute;left:-9999px;font-size:72px";
  span.textContent = "mmmmmmmmmmlli";
  body.appendChild(span);
  const width = font => { span.style.fontFamily = font; return span.offsetWidth; };
  const bases = ["monospace", "sans-serif", "serif"], baseWidths = bases.map(width);
  fp.fonts = FONTS.filter(f => bases.some((b, i) => width(`'${f}', ${b}`) !== baseWidths[i])).join(", ");
  span.remove();
  return fp;
}""".replace("FONTS", json.dumps(FONTS))


def normalize(raw: dict) -> dict:
    """Replaces the canvas image with its hash, so files stay small and comparable."""
    fp = dict(raw)
    canvas = fp.pop("canvas", None)
    fp["canvasHash"] = hashlib.sha256(canvas.encode()).hexdigest()[:16] if canvas else None
    return dict(sorted(fp.items()))


LOCAL_URL = "https://fingerprint-audit.invalid/"  # answered by Playwright itself, never sent anywhere


def collect(context) -> dict:
    """Fingerprint of a Playwright context (a persona profile).

    The page must be a secure context like the user's 127.0.0.1 page, otherwise Chrome
    hides userAgentData and deviceMemory and the profiles only look different.
    """
    page = context.pages[0] if context.pages else context.new_page()
    page.route(LOCAL_URL, lambda route: route.fulfill(content_type="text/html", body="<!doctype html><body></body>"))
    try:
        page.goto(LOCAL_URL)
        return normalize(page.evaluate(PROBE_JS))
    finally:
        page.unroute(LOCAL_URL)


# --- the user's own Chrome: a one-shot page on 127.0.0.1 ---

_PAGE = """<!doctype html><meta charset="utf-8"><title>Fingerprint audit</title>
<body style="font:16px sans-serif;margin:2em"><p id="msg">Collecting (stays on this computer)...</p>
<script>(PROBE)().then(fp => fetch("SUBMIT", {method: "POST", body: JSON.stringify(fp)}))
.then(() => { document.getElementById("msg").textContent = "Done. You can close this tab."; })
.catch(e => { document.getElementById("msg").textContent = "Failed: " + e; });</script>"""


def serve_probe(timeout: float = 300, on_ready=print) -> dict | None:
    """Serves the probe once on 127.0.0.1 and waits for the user's Chrome to send its fingerprint.

    The random token in the path keeps other local pages from posting to it.
    """
    token = secrets.token_urlsafe(12)
    result: dict = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != f"/{token}":
                self.send_error(404)
                return
            body = _PAGE.replace("(PROBE)", f"({PROBE_JS})").replace("SUBMIT", f"/{token}/submit").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if self.path != f"/{token}/submit":
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length") or 0)
            try:
                result.update(json.loads(self.rfile.read(length)))
            except json.JSONDecodeError:
                self.send_error(400)
                return
            self.send_response(204)
            self.end_headers()

        def log_message(self, *args):  # keep the console quiet
            pass

    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        server.timeout = 1
        on_ready(f"http://127.0.0.1:{server.server_address[1]}/{token}")
        deadline = time.monotonic() + timeout
        while not result and time.monotonic() < deadline:
            server.handle_request()
    return normalize(result) if result else None


# --- storage and comparison ---

def _dir(day: date, directory: Path | None = None) -> Path:
    return (directory or config.DATA_DIR / "eval" / "fingerprints") / day.isoformat()


def save(label: str, fp: dict, day: date, directory: Path | None = None) -> Path:
    path = _dir(day, directory) / f"{label}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(fp, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def load_day(day: date, directory: Path | None = None) -> dict[str, dict]:
    folder = _dir(day, directory)
    if not folder.is_dir():
        return {}
    return {path.stem: json.loads(path.read_text(encoding="utf-8")) for path in sorted(folder.glob("*.json"))}


def latest_mine(directory: Path | None = None) -> dict | None:
    """The user's own Chrome fingerprint from the most recent audit, if one was recorded."""
    root = directory or config.DATA_DIR / "eval" / "fingerprints"
    files = sorted(root.glob("*/mine.json")) if root.is_dir() else []
    return json.loads(files[-1].read_text(encoding="utf-8")) if files else None


@dataclass
class Comparison:
    labels: list[str]
    rows: dict[str, list]  # attribute -> value per label
    same: list[str]  # attributes equal in every profile
    pairs: dict[tuple[str, str], float]  # share of attributes two profiles have in common

    @property
    def score(self) -> float:
        """Share of attributes that are identical everywhere: 1.0 = indistinguishable, hence linkable."""
        return len(self.same) / len(self.rows) if self.rows else 0.0


def compare(fingerprints: dict[str, dict]) -> Comparison:
    labels = list(fingerprints)
    keys = sorted({key for fp in fingerprints.values() for key in fp})
    rows = {key: [fingerprints[label].get(key) for label in labels] for key in keys}
    same = [key for key, values in rows.items() if all(v == values[0] for v in values)]
    pairs = {}
    for a, b in combinations(range(len(labels)), 2):
        equal = sum(1 for values in rows.values() if values[a] == values[b])
        pairs[(labels[a], labels[b])] = equal / len(rows) if rows else 0.0
    return Comparison(labels, rows, same, pairs)


def _short(value, width: int = 28) -> str:
    text = "-" if value is None else str(value)
    return text if len(text) <= width else text[: width - 1] + "…"


def format_report(comparison: Comparison, names: dict[str, str] | None = None) -> str:
    names = names or {}
    titles = [names.get(label, label) for label in comparison.labels]
    key_width = max([len(k) for k in comparison.rows] + [9])
    lines = ["  ".join(["attribute".ljust(key_width), "same"] + [_short(t, 28).ljust(28) for t in titles])]
    for key, values in comparison.rows.items():
        mark = " =  " if key in comparison.same else " ≠  "
        lines.append("  ".join([key.ljust(key_width), mark] + [_short(v).ljust(28) for v in values]))
    lines.append("")
    lines.append(f"Identical in all {len(titles)} profiles: {len(comparison.same)}/{len(comparison.rows)}"
                 f" attributes (linkability score {comparison.score:.2f}; 1.00 = cannot be told apart)")
    for (a, b), share in comparison.pairs.items():
        lines.append(f"  {names.get(a, a)} ~ {names.get(b, b)}: {share:.0%} same")
    lines.append("Plus: every profile shares the same IP address until traffic goes through Nym (phase 6).")
    return "\n".join(lines)
