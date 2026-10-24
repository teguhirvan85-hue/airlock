# Airlock

**Let agents read the outside world without handing them the keys.**

Airlock is a triage workflow built on [Switch](https://github.com/sandbox-quantum/switch). The agent that reads untrusted text, a GitHub issue for example, and the agent that can change your repository never share a room. A person stands between them, and every action that leaves the machine waits for a named approval.

```
 airlock room                         workshop room
 ┌───────────────────────────┐        ┌──────────────────────────────┐
 │ raw issue (untrusted)     │        │ clean card only              │
 │   ↓                       │ person │   ↓                          │
 │ Reader  (no file, shell   │ ─────▶ │ Fixer (works in the repo,    │
 │          or web tools)    │forwards│        tests, commits)       │
 │   ↓ JSON summary          │  card  │   ↓                          │
 │ Gatekeeper (no model:     │        │ "push + open PR?" ──▶ person │
 │   fixed schema check)     │        │   Allow once / Decline       │
 └───────────────────────────┘        └──────────────────────────────┘
```

## Why

An agent becomes dangerous when three things are true at once ([Switch docs: working safely with agents](https://docs.switchagents.ai/switch-rooms/resources/working-safely-with-agents)): it acts without anyone watching, it can reach something that matters, and it takes in content nobody checked. One coding agent in one channel, reading issues, usually has all three.

Airlock breaks each one with a Switch primitive rather than with a smarter prompt:

| Risk factor | What breaks it | Switch primitive |
|---|---|---|
| Takes in unchecked content | Raw text stops in the airlock room. The Fixer is not a member there, so it never reads it. | Room membership is what grants reading |
| Reaches something that matters | The Reader has no file, shell or web tools and an empty working directory. | Per-agent tools and directory |
| Acts without anyone watching | Push and pull request wait for a person; Switch records who chose. | Approvals in the room |
| Free text riding along | The Gatekeeper accepts only a fixed JSON shape: no links, mentions, markup, hidden characters, or paths outside the repo. | A custom agent on the agent protocol |
| Agents calling each other | Both agents take instructions only from their owner. | Addressing (`owner`) |

The claim is not "the Reader spots every attack". It is that a fooled Reader still has nothing to leak and nobody to tell.

## What is in this repository

| Path | What it is |
|---|---|
| `template/airlock.template.yaml` | The Switch template: both rooms, both agents, one group. |
| `agents/gatekeeper/` | The Gatekeeper: `summary_schema.py` (the check, with tests) and `gatekeeper.py` (the agent). |
| `agents/lib/` | A small stdlib-only client for custom Switch agents (register, WebSocket, post). |
| `scripts/register_gatekeeper.sh` | Registers the Gatekeeper under your own account on a local Switch server. |
| `target/` | Kedai, a tiny shop with one real bug, used as the repository the Fixer works on. `.env` holds fake canary secrets. |

## Run it in about 10 minutes

You need macOS, Docker Desktop, [Switch Console](https://github.com/sandbox-quantum/switch/releases/latest) and Claude Code signed in (`claude auth login`).

1. **Start Switch.** Open Switch Console, choose *On this computer*, and start the server. Mattermost runs in Docker and comes with it.
2. **Make the Fixer's repository.** Copy `target/` somewhere, `git init` it and commit. Push it to a GitHub repository of yours if you want the pull-request step.
3. **Register the Gatekeeper.**
   ```
   bash scripts/register_gatekeeper.sh
   ```
   It signs in to your local server, mints a personal registration key and saves the agent's key to `~/.airlock/agents/` (mode 600). Nothing secret is printed.
4. **Create the rooms and agents.** In Switch Console: *Templates → Import template*, pick `template/airlock.template.yaml`, then *Use template*. Set *Target repository* to the folder from step 2 and choose *Create all*.
5. **Start the Gatekeeper.**
   ```
   python3 agents/gatekeeper/gatekeeper.py airlock-reader,Reader airlock
   ```
6. **Triage an issue.** In the `airlock` channel, mention `@airlock-reader` with the issue text. The Gatekeeper answers in the thread with a checked card. Paste the card into `workshop` with `@airlock-fixer`. Approve the push and pull request when the Fixer asks.

Address agents by their username (`@airlock-reader`), not their display name.

## Tests

```
cd agents/gatekeeper && python3 -m unittest -v
cd target && python3 -m unittest -v
```

## Honest limits

- **Room isolation depends on Switch.** Today an agent already in a room can invite another agent into it. Both agents here take instructions only from their owner, and [switch#805](https://github.com/sandbox-quantum/switch/issues/805) (with an open fix) makes invites respect that rule.
- **Mattermost puts every bot in the team's default channels** (Town Square, Off-Topic). The agents are members there too, but take instructions only from their owner; the airlock text never goes there.
- **Switch's own tools stay with every agent**, including the Reader. The Gatekeeper and the owner-only addressing are what stop a fooled Reader from using them to reach the Fixer.
- **The Gatekeeper limits shape, not meaning.** A summary can still be wrong. That is why a person reads the card before forwarding it.
- **A request raised during a room's template kickoff** can fail to reach Mattermost ("Invalid RootId"), leaving the agent waiting. The kickoffs here only ask agents to introduce themselves, so no approval is ever raised there.
