# Routing decoys through the Nym mixnet

Without a proxy every persona shares the user's IP address, and the fingerprint audit
showed that all profiles also share one device fingerprint. Routing decoy traffic
through the [Nym](https://nym.com) mixnet gives personas other addresses. It also
changes what the decoys can hide, so first choose a setup.

## Three setups

| Setup | How | Sites and trackers | Internet provider |
|---|---|---|---|
| **1. Decoys only** | one `nym-socks5-client`, all personas use it | personas no longer come from the user's address | sees the user's real traffic in the clear; the decoys are hidden inside the mixnet and no longer cover it |
| **2. Everything** | the NymVPN app for the whole computer, no proxy in the agent | the user and the personas leave through the same exit address | sees only mixnet traffic |
| **3. Both** | the user on NymVPN; each persona its own `nym-socks5-client`, each with a different exit | every identity has its own address | sees only mixnet traffic |

Setup 1 helps against sites and trackers but gives up hiding from the internet
provider, which is what decoys do best. Setup 3 helps against both and is the one
the agent is built for; setup 1 is the same code with one proxy.

What none of them change: every profile still has the same device fingerprint (screen,
graphics card, fonts), so a tracker that links by fingerprint can still connect them.
The mixnet's exit country also differs from the city a persona "lives" in.

## The agent's side

The agent never downloads or starts Nym software. It only connects to a SOCKS5 address
that you give it, stored in `data/proxies.json` on this machine:

```powershell
# setup 1: every persona through one client
.venv\Scripts\python.exe -m agent.proxy set default socks5://127.0.0.1:1080

# setup 3: each persona through its own client
.venv\Scripts\python.exe -m agent.proxy set <persona-id> socks5://127.0.0.1:1081

.venv\Scripts\python.exe -m agent.proxy status
.venv\Scripts\python.exe -m agent.proxy unset <persona-id|default>
```

- **Fail-closed:** once a persona has a proxy, it never goes online without it. If the
  proxy does not answer, its sessions are skipped and a Windows notification says so
  (once a day). Otherwise the persona would appear from the user's own address on that
  day, which would link the two.
- Chrome sends host names to the proxy, so DNS lookups go through the mixnet too.
- Through a proxy, page timeouts are three times longer and YouTube is skipped (video is
  too slow through a mixnet).

## The Nym side (done by you)

Download `nym-socks5-client` for Windows only from Nym's official releases (linked from
the [Nym documentation](https://nym.com/docs/developers/clients/socks5)). Each client
needs an id and a provider: a Network Requester address, which every Exit Gateway runs;
the documentation points to the Harbourmaster site to look them up.

```powershell
# one client per persona for setup 3; give each a different exit gateway's address
nym-socks5-client.exe init --id persona-1 --provider <network requester address> --use-reply-surbs true
nym-socks5-client.exe run --id persona-1
```

A client listens on `127.0.0.1:1080` by default. For setup 3 each client needs its own
port: the documentation does not name the option, so check
`nym-socks5-client.exe init --help`, or the port in the client's `config.toml`
(`%USERPROFILE%\.nym\socks5-clients\<id>\config\config.toml`), then give each persona
its port with `agent.proxy set`.

For setup 3, also install NymVPN for your own traffic and, with its split tunneling
(beta on Windows), leave the `nym-socks5-client` processes out of the VPN; otherwise
decoy traffic would go through the mixnet twice.

## Checking it

`python -m agent.evaluation fingerprint` still records the same device fingerprint
(the proxy does not change it); `python -m agent.evaluation trackers` shows which
third parties saw several personas, now from different addresses.
