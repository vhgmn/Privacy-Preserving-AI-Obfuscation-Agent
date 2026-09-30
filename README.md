# Privacy-Preserving AI Obfuscation Agent

A desktop agent that protects your online privacy by surrounding your real browsing
with believable decoy activity. Instead of random noise, which trackers can filter
out, it creates coherent **personas**: people with their own interests, short-lived
projects and habits, who will browse the web on their own schedule.

All planning runs on a **local LLM** ([Ollama](https://ollama.com)), so no cloud
service ever learns which traffic is fake.

> **Status:** early development. The Persona Engine (phase 1) works; the browser
> agent and scheduler are not built yet. See the [roadmap](#roadmap).

## Principles

- **Local only.** Personas, plans and logs are generated and stored on your machine
  and are never committed (`data/` and `profiles/` are in `.gitignore`).
- **Secure even when the code is public** (Kerckhoffs's principle). The repository
  contains no fixed personas, query lists or timing profiles; every install
  generates its own.
- **No harm to the user.** Decoys never touch topics a tracker could hold against
  the user: health, sexuality, political extremism, illegal activity, financial
  distress, religion. Every topic is checked by the LLM and rejected if in doubt.
- **No harm to others.** No CAPTCHA bypassing, no collection of other people's data.
- **Separate identities.** Each persona will get its own persistent browser
  profile, so decoy cookies and history never mix with each other.

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
1–6 weeks, like in a real person's life.

## Requirements

- Windows 10/11 (other platforms untested)
- Python 3.12
- [Ollama](https://ollama.com) with the model pulled: `ollama pull qwen2.5:3b`
- Google Chrome (used by the future browser agent)

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

.venv\Scripts\python.exe -m agent.persona list
.venv\Scripts\python.exe -m agent.persona show <id>
.venv\Scripts\python.exe -m agent.persona delete <id>
```

## Tests

The tests use a fake LLM, so they run in under a second and need no Ollama:

```powershell
.venv\Scripts\python.exe -m unittest discover -v
```

## Project layout

```
agent/
  llm.py              Ollama client
  config.py           paths, persona count, cities, interest areas, excluded topics
  persona/
    schema.py         Persona / Interest / Behavior with strict validation
    generator.py      staged LLM generation with safety checks and retries
    store.py          atomic JSON storage in data/personas/
    names.py          Armenian first names
    __main__.py       command line
tests/                unit tests
```

## Roadmap

0. ✅ Setup: local LLM, dependencies, real Chrome via Playwright
1. ✅ **Persona Engine**
2. Daily Planner: search journeys (query → click → read → follow-up) per persona
3. Browser Agent: human-like typing, scrolling and reading time; one Chrome profile per persona
4. Scheduler: bursty, log-normal sessions within the user's waking hours; Windows idle detection
5. Evaluation: an LLM "tracker's eye view" and a real-vs-decoy classifier
6. Optional routing of decoy traffic through the Nym mixnet
7. Threat model and ethics documentation

## License

[MIT](LICENSE)
