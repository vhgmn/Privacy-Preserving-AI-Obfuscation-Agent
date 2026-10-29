# Privacy-Preserving AI Obfuscation Agent

A desktop agent that protects your online privacy by surrounding your real browsing
with believable decoy activity. Instead of random noise, which trackers can filter
out, it creates coherent **personas**: people with their own interests, short-lived
projects and habits, who will browse the web on their own schedule.

All planning runs on a **local LLM** ([Ollama](https://ollama.com)), so no cloud
service ever learns which traffic is fake.

> **Status:** research prototype, all phases in place. Personas, daily plans, the
> browser agent and the scheduler work; the scheduler is started by hand. The
> evaluation tools work and their first results are below and in the
> [threat model](docs/THREAT_MODEL.md); results over a longer run are still to come.
> Routing through Nym is built but not yet tried with a live Nym client. See the
> [roadmap](#roadmap).
>
> **Read first:** [what this protects and what it does not](docs/THREAT_MODEL.md),
> and [the rules it follows](docs/ETHICS.md). In short: decoys make a profile less
> certain; they do not make you invisible, and all browser profiles on one computer
> share one fingerprint.

## Principles

- **Local only.** Personas, plans and logs are generated and stored on your machine
  and are never committed (`data/`, `profiles/` and `models/` are in `.gitignore`).
- **Secure even when the code is public** (Kerckhoffs's principle). The repository
  contains no fixed personas, query lists or timing profiles; every install
  generates its own.
- **No harm to the user.** Decoys never touch topics a tracker could hold against
  the user: health (including diets), sexuality, political extremism, illegal
  activity, financial distress, religion. Every topic is checked by the LLM and
  rejected if in doubt.
- **No harm to others.** No CAPTCHA bypassing, no ad clicks, no forms or sign-ins, no
  collection of other people's data ([details](docs/ETHICS.md)).
- **Separate identities.** Each persona has its own persistent browser profile, so
  decoy cookies and history never mix with each other or with yours.

## How the Persona Engine works

1. **Code** randomly picks the structure: age group, city in Armenia (personas share
   the user's IP, so they must plausibly live in the same country), language mix
   (Armenian / Russian / English), a name from a list, and the broad areas of
   interest (e.g. "music", "planning a trip abroad"). Numeric habits such as reading
   speed, typo rate and patience are sampled from realistic distributions, because a
   small LLM tends to give every persona the same numbers.
2. **The local LLM** (`qwen2.5:3b` by default) fills in the meaning: occupation,
   search style, a short bio, and one concrete topic per area.
3. **Checks:** every topic goes through a separate safety check (temperature 0),
   duplicates across personas are rejected, and vague topics are replaced. The
   result is validated against a strict schema before it is saved to
   `data/personas/<id>.json`.

Long-term interests persist; projects (e.g. "choosing a new fridge") expire after
1–6 weeks, like in a real person's life. `refresh` replaces each finished project
with a new one that fits the persona and remembers the last ten finished projects,
so the new ones do not repeat them.

**Favourite sites** grow out of the persona's own reading, so no list of sites ships
with the code: each morning, a site where it read a page for a minute or more, or read
on into, in the last two weeks may become a favourite (30% chance a day, at most five),
and each favourite is forgotten again with a 3% chance a day.

## How the Daily Planner works

Every day each persona gets 2–5 **sessions**. A session is one search journey on one
topic: a first search, 1–3 follow-up searches that refine it, results to open and
read, and sometimes a YouTube search. Topics are picked by weight; projects come up
more often (at most twice a day), and now and then a finished project returns.
Each session uses one of the persona's languages and its search style.
Once a persona has favourite sites, 40% of its sessions also stop by one (before or
after the search), and 20% are only a look at one or two favourites, with no search,
as people check the news.

The plan says *what* to do, not *when*: timing belongs to the scheduler (phase 4).
Follow-up searches are marked replaceable, so the browser agent (phase 3) can swap
them for ones based on what the real results showed.

Every query is safety-checked and must be written in the session's language: an
Armenian query with Cyrillic or stray foreign letters is rejected.

## How the Browser Agent works

A planned session is carried out in **real, visible Google Chrome**, in a persistent
profile that belongs to that persona only (`profiles/<persona-id>/`), so each persona
builds up its own cookies and history.

- **Typing** goes letter by letter with irregular pauses; at the persona's typo rate a
  wrong letter slips in and is deleted again a moment later.
- **The mouse** moves along curved paths to what it clicks; **scrolling** comes in
  irregular bursts, now and then back up.
- **Reading time** follows the page length, the persona's reading speed and patience
  (15 s to 5 min, with no fixed limit that many reads would end at). A page already
  read in the session is not opened again.
- **Reading on:** after a page, the persona often follows up to three links to other
  articles on the same site, more often the more patient it is (never menus, logins,
  files or other sites, and not inside social networks, which only show a login wall).
- **YouTube** videos are watched for 30 s to 3 min; Chrome runs muted.
- Chrome starts without the automation flag, so `navigator.webdriver` is `false`
  (check with `python -m agent.browser check <persona-id>`). No other "stealth"
  tricks are used.

Rules the agent never breaks:

- On a **CAPTCHA or bot check it stops** the session and reports it. It never tries
  to solve or get around one. (A mere "protected by reCAPTCHA" on an ordinary page,
  e.g. under a contact form, does not count.)
- On a **cookie banner it only ever clicks "Reject"**; if there is none, it leaves the
  banner alone.
- It **never fills in forms, signs in, downloads files or clicks ads**.

What it did is written to `data/logs/<date>/<persona-id>.jsonl` (searches, pages and
reading times; no screenshots), for the evaluation phase.

## How the Scheduler works

`python -m agent.scheduler run` keeps running in a terminal and checks once a minute
whether it is time for a session.

- **Each day** it first refreshes the personas' projects and plans the day.
- **Your rhythm:** it notes in which hours you use the computer (keyboard or mouse
  input in the last 2 minutes, via Windows `GetLastInputInfo`) and, after a week,
  only runs sessions in your usual waking hours (08:00–23:00 until then). This record
  describes you, so it stays in `data/activity/` and never leaves the machine.
- **Timing** is bursty like real browsing: the pause before the next session is
  log-normal, from minutes to hours, and shrinks when many sessions are left.
- **Mostly while you are active**, so your real traffic is mixed with decoy traffic.
  When you are away, a due session runs only sometimes (about 30%), and then short:
  one search, one page.
- **Out of your way:** Chrome starts off-screen and is moved to its own Windows
  virtual desktop, *Decoy browsing* (see it with Win+Tab). Pages are driven through
  the DevTools protocol, so the window never needs focus.
- **One browser at a time** (the state file also records who is using Chrome, so the
  fingerprint audit and the scheduler never open a profile twice), and what is done is saved in `data/state/<date>.json`, so
  a restart never repeats a session.
- **On a bot check** the persona rests until the next day, all searches pause for
  3 hours, and a Windows notification tells you.

## Evaluation: fingerprint audit

Separate profiles keep cookies apart, but a site can also recognise a browser by its
**fingerprint**: user agent, screen, time zone, languages, graphics card, fonts, how
it draws a test image, and so on. `python -m agent.evaluation fingerprint` records
these in every persona's Chrome; `--mine` records your own Chrome through a one-time
page on `127.0.0.1` (open the printed address in your usual Chrome). Nothing goes to
an external fingerprinting site; the results stay in `data/eval/fingerprints/`.
`fingerprint-report` compares them.

First result (one laptop, Chrome 155): the personas and the user's own Chrome were
identical in 23 of 24 attributes. The only difference was the language list: fresh
profiles had Chrome's default, the user's Chrome had their own. That is the one thing
that would let a tracker tell the user apart from the decoys on the same device, so
each persona's Chrome now gets the user's language list (taken from the latest
`--mine` audit, kept local). After that: 24 of 24.

What this means: until traffic goes through Nym, **all profiles share one IP address
and one device fingerprint**, so a tracker that links by fingerprint sees one device.
The decoys then do not pose as separate people; they blend into the same device's
traffic, where the user's own browsing must not stand out.

## Evaluation: tracker's eye view

`python -m agent.evaluation tracker` lets the local LLM play a tracker. It sees only
what one could see in a persona's logs of the last 7 days, in two views: an
**advertising network** (pages read with their titles, how long, YouTube searches)
and a **search engine** (the searches). The topic and language the planner chose are
the answer, so they are never shown. For each view the LLM:

- builds an ad profile: age group, gender, city, job, interests, current projects;
- rates from 0 to 10 how automated the visitor looks, and why;
- names any excluded category (health, debt, ...) the visitor seems interested in.
  Any hit is a problem in the decoys, since a tracker would pin it on the user.

A separate judge call per guessed interest checks it against the persona's real
interests, giving **recall** (how many real interests were found: high means the
persona is coherent) and **precision**. Code also measures signs of automation that
a small LLM misses: how bursty the pauses between sessions are, night sessions,
reading times that repeat exactly, pages and searches repeated. Results go to
`data/eval/tracker/<date>/`.

The first test run, on a day of logs, already found two weaknesses that are now
fixed: most reads ended at exactly the same 240 s limit, and "healthy" or "diet"
recipe searches passed the safety check although a tracker files them under health.

## Evaluation: real vs. decoy, as the network sees it

An internet provider or Wi-Fi owner sees which domains are visited and when, from every
profile alike. `python -m agent.evaluation classifier` asks whether such an observer
could tell the user's browsing sessions from the decoys. The user's side is read from
their own Chrome history, **only the time and domain of each visit**, from a temporary
copy that is deleted at once; nothing about it is written to disk, only the aggregate
result (`data/eval/classifier/<date>.json`).

Visits are cut into sessions at pauses of 30 minutes. Each session is described by its
hour, weekday, length, pages, domains, pauses between pages, share of searches and of
YouTube, and how many of its domains the same source had not visited before. A logistic
regression and a random forest are scored by cross-validated **AUC** (0.5: the
observer cannot tell them apart; 1.0: always can), and the forest names the features
that give the decoys away.

First result (7 days, 32 sessions of the user, 8 decoy sessions): **AUC 0.91–0.97**,
so decoys were easy to spot. They were short (8 vs. 95 minutes), had few pages (7 vs.
74), mostly searches (51% vs. 27%) and mostly new sites (67% vs. 27%). This led to
reading on inside sites and to favourite sites (see above). Two cautions: eight decoy
sessions are few, and a real provider merges all traffic of one address, so decoys
that run while the user is active blend into the user's own sessions; the test is
deliberately strict.

## Evaluation: which trackers see the personas

During every session the agent notes which other companies' domains each page made
Chrome contact (only the domain and the site it was loaded on), e.g. an analytics
script on a news site. `python -m agent.evaluation trackers` then lists, per persona,
the third parties that saw it on two or more sites (they can follow it from site to
site) and those that saw several personas: since all profiles share one device
fingerprint and one IP address, such a tracker can tell they are one device.

## Optional: decoys through the Nym mixnet

Each persona, or all of them, can go online through a SOCKS5 proxy such as a local
[Nym](https://nym.com) client, so they no longer share the user's IP address. A persona
with a proxy never goes online without it (fail-closed). Which setup hides what, and
how to set up the Nym side, is in [docs/NYM.md](docs/NYM.md).

## Armenian queries

The small LLM writes good English and Russian but cannot write Armenian (it invents
words), so Armenian queries are written in English and translated locally with
Meta's [NLLB-200](https://huggingface.co/facebook/nllb-200-distilled-600M)
(distilled 600M, converted to CTranslate2 int8, ~600 MB, about 2 s per batch on a CPU).
Without the model, Armenian sessions are replaced by Russian or English ones and a
warning is shown.

The model is converted on your machine from Meta's official repository (rather than
downloading a third-party conversion). It needs about 5 GB temporarily; use a short
folder path, since pip fails on Windows paths near 260 characters:

```powershell
py -3.12 -m venv C:\nllb_tmp\venv
C:\nllb_tmp\venv\Scripts\python.exe -m pip install --index-url https://download.pytorch.org/whl/cpu torch
C:\nllb_tmp\venv\Scripts\python.exe -m pip install transformers ctranslate2 sentencepiece protobuf
$env:HF_HOME = "C:\nllb_tmp\hf-cache"
C:\nllb_tmp\venv\Scripts\ct2-transformers-converter.exe --model facebook/nllb-200-distilled-600M `
    --quantization int8 --copy_files sentencepiece.bpe.model --output_dir models\nllb-600m-int8
Remove-Item -Recurse -Force C:\nllb_tmp
```

Note: the NLLB weights are licensed **CC-BY-NC-4.0** (non-commercial use only); they
are not part of this repository, whose code is MIT.

## Requirements

- Windows 10/11 (other platforms untested)
- Python 3.12
- [Ollama](https://ollama.com) with the model pulled: `ollama pull qwen2.5:3b`
- Optional, for Armenian queries: the NLLB model (see [Armenian queries](#armenian-queries))
- Google Chrome (the browser agent drives your installed Chrome, not a bundled browser)

Keep Ollama on local models only; do not enable network exposure.

## Setup

```powershell
git clone https://github.com/vhgmn/Privacy-Preserving-AI-Obfuscation-Agent.git
cd Privacy-Preserving-AI-Obfuscation-Agent
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Do not clone into a cloud-synced folder (OneDrive, Dropbox): personas and browser
profiles would be uploaded.

## Usage

```powershell
# create personas up to the target (3); about a minute each on a CPU
.venv\Scripts\python.exe -m agent.persona generate

# replace projects whose time is over with new ones (run every few days)
.venv\Scripts\python.exe -m agent.persona refresh

.venv\Scripts\python.exe -m agent.persona list
.venv\Scripts\python.exe -m agent.persona show <id>
.venv\Scripts\python.exe -m agent.persona delete <id>

# plan today's sessions for every persona (a few minutes on a CPU)
.venv\Scripts\python.exe -m agent.planner today
.venv\Scripts\python.exe -m agent.planner show <persona-id> [--date YYYY-MM-DD]

# carry out one planned session in the persona's Chrome (a few minutes; don't touch the window)
.venv\Scripts\python.exe -m agent.browser check <persona-id>
.venv\Scripts\python.exe -m agent.browser run <persona-id> --session 1 [--date YYYY-MM-DD]

# fingerprint audit: every persona profile, then your own Chrome (open the printed address), then compare
.venv\Scripts\python.exe -m agent.evaluation fingerprint
.venv\Scripts\python.exe -m agent.evaluation fingerprint --mine
.venv\Scripts\python.exe -m agent.evaluation fingerprint-report

# tracker's eye view of every persona's last 7 days of logs (a few minutes per persona)
.venv\Scripts\python.exe -m agent.evaluation tracker [<persona-id>] [--days 7]

# can a network observer tell your sessions from the decoys? (reads only time and domain of your visits)
.venv\Scripts\python.exe -m agent.evaluation classifier [--days 7]

# which third parties saw each persona, on how many sites
.venv\Scripts\python.exe -m agent.evaluation trackers [--days 7]

# optional: send a persona (or 'default': all) through a SOCKS5 proxy such as Nym, see docs/NYM.md
.venv\Scripts\python.exe -m agent.proxy set <persona-id|default> socks5://127.0.0.1:1080
.venv\Scripts\python.exe -m agent.proxy status

# let the agent run on its own schedule (leave the terminal open; Ctrl+C stops)
.venv\Scripts\python.exe -m agent.scheduler run
.venv\Scripts\python.exe -m agent.scheduler status
.venv\Scripts\python.exe -m agent.scheduler next   # run one pending session now, for trying out
```

## Tests

The tests use a fake LLM and a fake translator, so they run in under a second and
need neither Ollama nor the NLLB model:

```powershell
.venv\Scripts\python.exe -m unittest discover -v
```

## Project layout

```
agent/
  llm.py              Ollama client
  translate.py        local English → Armenian translation (NLLB, CTranslate2)
  config.py           paths, persona count, cities, interest areas, excluded topics
  proxy.py            optional SOCKS5 proxy per persona (data/proxies.json), fail-closed
  persona/
    schema.py         Persona / Interest / Behavior with strict validation
    generator.py      staged LLM generation with safety checks and retries
    store.py          atomic JSON storage in data/personas/
    names.py          Armenian first names
    favorites.py      favourite sites learned from the persona's own reading
    __main__.py       command line
  planner/
    schema.py         DailyPlan / Session / Step, step order and language checks
    planner.py        topic, language and query choice for each session
    store.py          atomic JSON storage in data/plans/<date>/
    __main__.py       command line
  browser/
    human.py          typing, typos, mouse paths, scrolling, reading and watching times
    sites.py          DuckDuckGo, result pages and YouTube; bot-check and cookie-banner handling
    runner.py         persona's Chrome profile, runs a session, writes the log
    thirdparty.py     which third-party domains each page contacted (domains only)
    __main__.py       command line
  scheduler/
    timing.py         when to run: waking hours, active vs. away, log-normal pauses
    rhythm.py         idle time from Windows and the learned daily rhythm
    state.py          what is done today (data/state/<date>.json)
    desktop.py        moves the decoy Chrome to its own virtual desktop
    notify.py         Windows toast notifications
    daemon.py         the loop: prepare the day, run sessions, pause on bot checks
    __main__.py       command line
  evaluation/
    fingerprint.py    local fingerprint probe, the one-time 127.0.0.1 page, comparison report
    tracker_view.py   the LLM as tracker and judge, automation signals measured by code
    classifier.py     real-vs-decoy sessions from Chrome history (time + domain) and decoy logs
    trackers.py       third parties that saw each persona, and those shared between personas
    __main__.py       command line
tests/                unit tests
docs/
  THREAT_MODEL.md     adversaries, defences, what was measured, what is not protected
  ETHICS.md           rules towards the user, websites and other people
  NYM.md              routing decoys through the Nym mixnet
```

## Roadmap

0. ✅ Setup: local LLM, dependencies, real Chrome via Playwright
1. ✅ **Persona Engine**
2. ✅ **Daily Planner**: search journeys (query → click → read → follow-up) per persona
3. ✅ **Browser Agent** (v1): human-like typing, scrolling and reading time; one Chrome profile per persona.
   Next: follow-up searches based on the real results
4. ✅ **Scheduler**: bursty, log-normal sessions within the user's waking hours; Windows idle detection;
   own virtual desktop. Next: start automatically at logon
5. ✅ **Evaluation**: fingerprint audit, LLM "tracker's eye view", real-vs-decoy classifier, third
   parties per persona. Next: results over a week of running; longer, multi-topic sessions
6. ✅ Optional routing of decoy traffic through the Nym mixnet: one proxy for all personas or
   one per persona ([docs/NYM.md](docs/NYM.md)); not yet tried with a live Nym client
7. ✅ [Threat model](docs/THREAT_MODEL.md) and [ethics](docs/ETHICS.md)

## License

[MIT](LICENSE)
