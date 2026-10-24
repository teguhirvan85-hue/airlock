"""Echo agent: replies in thread to every message that @mentions it.

    python3 echo_example.py <agent-name> <room name or id>

The agent must already be registered (see README.md) and be a member of the room.
"""

from __future__ import annotations

import signal
import sys

from switch_agent import SwitchAgent, SwitchError


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    name, room = sys.argv[1], sys.argv[2]
    agent = SwitchAgent.from_name(name)
    room_id = room if _looks_like_id(room) else agent.find_room(room)
    signal.signal(signal.SIGINT, lambda *_: agent.stop())
    print(f"{name} listening in {room_id} (Ctrl-C to stop)")

    def on_event(event: str, data: dict) -> None:
        if event == "connection_state":
            print(f"connected: rooms={data.get('rooms')} cursor={data.get('cursor')}")
        elif event in ("gap", "evicted", "subscription_changed"):
            print(f"{event}: {data}")

    try:
        for msg in agent.listen([room_id], on_event=on_event):
            if not msg.mentioned:
                continue
            print(f"[{msg.sender_name}] {msg.text}")
            agent.reply(msg, f"echo: {msg.text}")
    except SwitchError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def _looks_like_id(value: str) -> bool:
    return len(value) == 36 and value.count("-") == 4


if __name__ == "__main__":
    raise SystemExit(main())
