# Threat model

What the agent tries to protect, from whom, and how well it does, as far as the
evaluation (phase 5) has measured it. Numbers come from one laptop over the first days
of running; they are a starting point, not a proof.

## What is protected

The user's **interests** as they show in their browsing: what a tracker, a data broker
or an internet provider would put into an advertising or risk profile of them. The
agent does not try to hide *that* the user browses, or the content of encrypted pages.

## Assumptions

- The user's computer is not compromised. Personas, plans, logs, the learned daily
  rhythm and the evaluation results live in `data/` and `profiles/` on that computer
  and nowhere else.
- The code is public (Kerckhoffs's principle): an adversary knows how personas,
  plans and timing are made. What the adversary does not know is generated on each
  install: the personas themselves, their topics, names, habits and favourite sites.
- All planning runs on a local LLM. No cloud service sees which traffic is fake.

## Adversaries

| Adversary | Sees | Goal |
|---|---|---|
| **Websites** | what a visitor does on their own site, the browser fingerprint, the IP address | profile their visitors |
| **Trackers and ad networks** (scripts on many sites) | the same, across every site that embeds them; cookies | follow a person from site to site |
| **Search engine** | the searches | profile the searcher |
| **Internet provider, Wi-Fi owner** | which domains are contacted and when (not the pages, with HTTPS), from every profile alike | profile the subscriber; sell or hand over that profile |
| **Someone with access to the computer** | everything | out of scope; such a person can also read the decoy logs |

## Defences and what was measured

**Coherent personas instead of random noise.** Random queries are easy to filter out.
Each persona has an age group, a city, a job, lasting interests and short projects,
and searches in its own languages and style. *Measured* (tracker's eye view, phase 5b):
a local LLM playing a tracker recovered the personas' real topics from their logs
(e.g. "cooking → DIY appetizers", "history → 19th century Armenian history").

**Separate browser profiles.** Each persona has its own Chrome profile, so cookies and
history never mix with each other or with the user's. *Measured* (fingerprint audit,
5a): this is **not enough on its own**. All profiles and the user's own Chrome were
identical in 24 of 24 fingerprint attributes once the language list was aligned
(before that, the language list was the one difference, and it singled out the user).
With the same IP address as well, a tracker that links by fingerprint sees **one
device**. The decoys then do not pose as separate people; they make that one device's
profile noisy, and the user's own browsing must not stand out within it.

**Human-like behaviour.** Typing with typos, curved mouse paths, scrolling bursts,
reading times from page length and patience, reading on inside a site, no
`navigator.webdriver` flag. *Measured*: the tracker's eye view rated the first decoys 6–7
out of 10 for "automated", from repeated identical pages and searches (caused by
manual test runs) and reading times that all ended at the same 240 s limit; the limit
is now random. No other "stealth" tricks are used.

**Timing.** Sessions follow the user's learned waking hours, mostly while the user is
active, with bursty, log-normal pauses. *Measured* (classifier, 5c): over the first week
an observer of domains and times told the user's sessions from the decoys with
**AUC 0.91–0.97**. Decoy sessions were short (8 vs. 95 minutes), had few pages (7 vs.
74), many searches and mostly new sites. Reading on inside sites and favourite sites
were added in response; their effect is still to be measured. The test is strict: a
real provider merges all traffic of one address, so decoys running during the user's
own sessions blend into them.

**Third parties.** *Measured* (5d): in one decoy session on five sites, Chrome contacted
87 third-party domains, seven of them on two or more of those sites (e.g. Google's
ad and analytics domains). Such trackers see the personas, which is the point; with
one fingerprint and one IP address they can also see that the personas are one device.

**Network address (optional).** Through the Nym mixnet, personas leave from other
addresses ([NYM.md](NYM.md)). Routing *only* the decoys hides them from the provider,
so they no longer cover the user's own traffic there; routing the user through NymVPN
as well (setup 3) hides both. This has not yet been tried with a live Nym client.

## What the agent does not protect against

- **Fingerprint linking.** Without changing the browser's fingerprint, which the agent
  deliberately does not do, a capable tracker can link every profile on the device.
- **Logged-in services.** Whatever the user does while signed in (mail, social
  networks, shopping accounts) is tied to the account, not the device; decoys add
  nothing there.
- **Content.** Unencrypted pages, and anything the user types into sites.
- **Topics the user really has.** Decoys add other interests; they do not remove the
  user's real ones from a profile. They work by making the profile less certain, not
  by deleting it.
- **A determined statistical adversary.** With enough data, differences in rhythm and
  session shape may remain (see the classifier result).
- **The decoys themselves.** A decoy topic can still be held against the user. For that
  reason sensitive topics are never used (see [ETHICS.md](ETHICS.md)).

## Data the agent keeps about the user

Kept on the computer only, never committed: the learned waking hours (`data/activity/`),
the latest fingerprint of the user's own Chrome (`data/eval/fingerprints/`, used to copy
its language list to the personas) and aggregate evaluation results. The classifier
reads the time and domain of the user's own visits from a temporary copy of Chrome's
history and writes nothing of them to disk.
