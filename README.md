# Privacy-Preserving AI Obfuscation Agent

A desktop agent that protects your online privacy by hiding your real behavior among
believable decoy activity. Instead of random noise, which trackers can filter out,
it generates coherent personas with their own interests, research journeys and daily
rhythm, and browses on their behalf.

All decoy planning runs on a **local LLM** (Ollama), so no third party ever knows
which traffic is fake.

> Status: early development.

## Principles

- **Local only.** Personas, plans and logs are generated and stored on your machine.
- **Secure even when the code is public.** Nothing in this repository is a fixed
  persona, query list or timing profile; every install generates its own.
- **No harm to others.** No CAPTCHA bypassing, no collection of other people's data.
