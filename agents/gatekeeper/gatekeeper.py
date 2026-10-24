"""Gatekeeper: checks every triage summary the Reader posts in the airlock room.

    python3 gatekeeper.py <reader names, comma-separated> [airlock room name]

It runs no model. A Reader message that carries a JSON summary is checked
against summary_schema; a pass is posted back as a clean card for a person to
forward to the workshop room, a refusal says why. Messages from anyone other
than the Reader are ignored.
"""

from __future__ import annotations

import re
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from summary_schema import Refused, check, render  # noqa: E402
from switch_agent import SwitchAgent, SwitchError  # noqa: E402

NAME = "airlock-gatekeeper"
DEFAULT_ROOM = "airlock"

# The Reader is told to post its summary as one ```json block.
JSON_BLOCK = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


def summary_in(text: str) -> str | None:
    found = JSON_BLOCK.search(text)
    return found.group(1) if found else None


def verdict(text: str) -> str | None:
    raw = summary_in(text)
    if raw is None:
        return None
    try:
        card = render(check(raw))
    except Refused as why:
        return f"✗ Refused: {why}. Nothing leaves this room."
    return f"✓ Passed the schema. Forward this card to the workshop room if it looks right:\n\n{card}"


def main() -> int:
    if len(sys.argv) not in (2, 3):
        print(__doc__)
        return 2
    readers = {name.strip() for name in sys.argv[1].split(",") if name.strip()}
    room_name = sys.argv[2] if len(sys.argv) == 3 else DEFAULT_ROOM

    agent = SwitchAgent.from_name(NAME)
    room_id = agent.find_room(room_name)
    signal.signal(signal.SIGINT, lambda *_: agent.stop())
    print(f"{NAME} watching {room_name} for summaries from {', '.join(sorted(readers))}")

    try:
        for msg in agent.listen([room_id]):
            if not readers & {msg.sender, msg.sender_name}:
                continue
            reply = verdict(msg.text)
            if reply:
                agent.reply(msg, reply)
    except SwitchError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
