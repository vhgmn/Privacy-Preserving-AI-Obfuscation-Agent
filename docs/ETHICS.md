# Ethics

Decoy browsing is a form of self-defence against profiling. It also touches other
people's systems, so the agent follows fixed rules. They are in the code, not only in
this document.

## Towards the user

- **No harmful topics.** A tracker files decoy interests under the user's name or
  device. Decoys therefore never touch topics that could be held against a person:
  health (including diets, weight loss and nutrition), sexuality or dating, political
  extremism or activism, illegal activity or weapons, debt or financial distress,
  religion. Every interest and every set of queries is checked by the local LLM and
  rejected when in doubt; the tracker's eye view checks again afterwards and flags any
  hit. (It did once: "healthy" and "diet" recipe searches passed the first check, so
  the rule was widened.)
- **No cloud.** Personas and plans are made by a local model. No outside service learns
  which traffic is fake, and none receives the user's data.
- **The user's data stays local.** The learned daily rhythm, the fingerprint of the
  user's own Chrome and the evaluation results never leave the computer and are never
  committed. The classifier reads only the time and domain of the user's own visits,
  from a temporary copy, and keeps nothing of them.
- **No accounts, no passwords.** The agent never signs in anywhere. If a persona should
  ever need an account, the user creates it by hand.

## Towards websites and their operators

- **No bot-check evasion.** On a CAPTCHA or "unusual traffic" page the agent stops the
  session, pauses that persona for the day and all searches for three hours, and tells
  the user. It never solves, skips or works around a check, and uses no "stealth"
  plugins; the only change to Chrome is removing the automation flag that ordinary
  browsers do not have.
- **No ad fraud.** The agent never clicks ads. Ad slots and sponsored boxes are left
  out when it chooses a link to follow. Its page views may still be counted as ad
  impressions, as any visitor's are; it creates no clicks or conversions.
- **No forms, downloads or purchases.** Nothing is submitted, bought, posted or
  downloaded. Login, sign-up, cart, checkout, subscription and file links are never
  followed.
- **Cookie banners: reject only.** If a banner has a reject button, the agent uses it;
  it never accepts on the user's behalf.
- **Light load.** A few sessions a day per persona, at human speed, one browser at a
  time: far less than one person browsing.

## Towards other people

- **No collection of other people's data.** The agent reads pages the way a visitor
  does and keeps only what it needs for the evaluation (searches, page addresses and
  titles, reading times, third-party domain names), on the user's computer. It does
  not scrape, store page contents or follow people.
- **No impersonation.** Personas are fictional: names come from a list of common first
  names, and nothing in them is taken from a real person. They do not post, comment or
  contact anyone.

## Honest limits

Decoys make a profile less certain; they do not make the user invisible (see the
[threat model](THREAT_MODEL.md)). The project publishes its own measurements,
including the unfavourable ones, so that nobody relies on it for more than it does.
