# Airlock: demo video script (2:00)

Screen recording of Mattermost and Switch Console, no voice-over needed: on-screen captions carry it. Cut every wait for an agent down to a beat.

| Time | On screen | Caption |
|---|---|---|
| 0:00–0:08 | The finished PR on GitHub, then the workshop room with "Chosen by @user". | **An agent fixed this bug and opened a PR. It never read the issue.** |
| 0:08–0:25 | Switch's own docs: the three-factor test. Then a single-room setup sketch: one agent, issues in, repo access. | **Read untrusted text + reach your repo + act unwatched = the dangerous agent. One room gives it all three.** |
| 0:25–0:40 | Switch Console: Templates → Airlock → What it creates (2 rooms, 2 agents). | **Airlock: two rooms. Membership decides who can read what.** |
| 0:40–1:00 | `airlock` channel: issue posted to @airlock-reader → Reader's JSON → Gatekeeper ✓ card in the thread. | **The Reader has no tools. The Gatekeeper runs no model: a fixed schema, nothing else gets out.** |
| 1:00–1:10 | The person copies the card into `workshop`. | **A person is the door.** |
| 1:10–1:35 | `workshop`: Fixer writes a failing test, fixes one line, 5 tests pass, commits on a branch. | **The Fixer works on the repo, and only sees the clean card.** |
| 1:35–1:50 | Approval card: push + open PR → Allow once → "Chosen by @user from Mattermost" → PR link. | **Anything leaving the machine waits for a named approval.** |
| 1:50–2:00 | Repo README top + one-line run command. | **Airlock. Built on Switch. `github.com/teguhirvan85-hue/airlock`** |

## Before recording

- Reset: recreate the rooms from the template so the channels are clean (no test messages).
- Start the Gatekeeper first; check it says "watching airlock".
- Mattermost in its own browser window at 1920×1080, sidebar visible, the green "Preview Mode" banner closed.
- Use the exact issue text from `docs/demo-issue.md` so the run matches the README.

## Optional segment (needs an attack issue from Teguh)

If an attack issue is added under `attacks/issues/`, replace 0:08–0:25 with a split screen: left, one agent in one room given that issue; right, the same issue in the airlock, Reader's summary refused or neutralised by the Gatekeeper. Keep it labelled as a simulation on screen.
