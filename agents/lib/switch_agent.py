"""Minimal client for custom agents that join Switch through the agent protocol.

Standard library only (Python 3.9+). The WebSocket client is a small RFC 6455
implementation below, so nothing has to be installed.

Wire contract (from switch_core/bridges/agent/api/handlers.py and
operations.py in github.com/sandbox-quantum/switch):

    base URL            the agent bridge, i.e. the switch-core API port
                        (local Switch Console server: "api" in ports.json)
    register            POST /agents                 Bearer <registration key>
    stream (WebSocket)  GET  /agents/{id}/connection/ws?connection_id=..&rooms=..
                        server -> {"event": ..., "data": {...}, "id": n}
                        server -> {"event": "ping", "data": {}} every 2 s
                        client -> {"type": "pong", "cursor": n}  (6 s TTL)
    post (no thread)    POST /agents/{id}/message    {"room_id", "content"}
    operations          GET  /agents/{id}/ops
                        POST /agents/{id}/ops/{name}  body = arguments,
                        header X-Switch-Connection-Id binds the caller's room
                        -> {"result": ...}

Everything except registration authenticates with the agent API key.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import queue
import socket
import ssl
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

__all__ = [
    "SwitchError",
    "SwitchHTTPError",
    "ConnectionRefused",
    "Message",
    "SwitchAgent",
    "default_base_url",
    "register_agent",
    "save_credentials",
    "load_credentials",
    "credentials_path",
]

CREDENTIALS_DIR = Path.home() / ".airlock" / "agents"
PORTS_FILE = (
    Path.home()
    / "Library"
    / "Application Support"
    / "switchdash"
    / "local-switch-server"
    / "ports.json"
)
CLIENT_NAME = "airlock-switch-agent"
CLIENT_VERSION = "0.1.0"


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


class SwitchError(Exception):
    """Base error for this library."""


class SwitchHTTPError(SwitchError):
    def __init__(self, status: int, detail: Any, method: str, path: str):
        self.status = status
        self.detail = detail
        super().__init__(f"{method} {path} -> {status}: {detail}")


class ConnectionRefused(SwitchError):
    """The server refused (or permanently ended) the event connection.

    `status` is the HTTP-equivalent status (401, 403, 409, ...) or None;
    `code` is the eviction code when the server sent one (e.g. "taken_over").
    """

    def __init__(self, message: str, status: Optional[int] = None, code: Optional[str] = None):
        self.status = status
        self.code = code
        super().__init__(message)


# --------------------------------------------------------------------------- #
# Base URL and credentials
# --------------------------------------------------------------------------- #


def default_base_url() -> str:
    """The agent bridge base URL.

    `SWITCH_BASE_URL` wins. Otherwise the local Switch Console server's
    "api" port from ports.json. The bridge is NOT the "gateway" port.
    """
    env = os.environ.get("SWITCH_BASE_URL")
    if env:
        return env.rstrip("/")
    try:
        ports = json.loads(PORTS_FILE.read_text())
        return f"http://127.0.0.1:{int(ports['api'])}"
    except (OSError, ValueError, KeyError) as exc:
        raise SwitchError(
            "Set SWITCH_BASE_URL (agent bridge URL); could not read the local "
            f"server's ports from {PORTS_FILE}: {exc}"
        ) from exc


def credentials_path(name: str) -> Path:
    return CREDENTIALS_DIR / f"{name}.json"


def save_credentials(name: str, agent_id: str, api_key: str, base_url: Optional[str] = None) -> Path:
    """Write ~/.airlock/agents/<name>.json with mode 600 (directory 700)."""
    CREDENTIALS_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(CREDENTIALS_DIR, 0o700)
    path = credentials_path(name)
    data = {"name": name, "agent_id": agent_id, "api_key": api_key}
    if base_url:
        data["base_url"] = base_url
    tmp = path.with_suffix(".json.tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return path


def load_credentials(name: str) -> Dict[str, str]:
    path = credentials_path(name)
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        raise SwitchError(
            f"No credentials for agent {name!r} at {path}. Register it first "
            "(register_agent / `python3 switch_agent.py register`)."
        ) from None


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #


def _http(
    base_url: str,
    method: str,
    path: str,
    token: str,
    body: Any = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 30.0,
) -> Any:
    url = base_url.rstrip("/") + path
    data = None
    hdrs = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode()
        hdrs["Content-Type"] = "application/json"
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            detail = json.loads(raw).get("detail", raw.decode(errors="replace"))
        except (ValueError, AttributeError):
            detail = raw.decode(errors="replace")
        raise SwitchHTTPError(exc.code, detail, method, path) from None
    if not raw:
        return None
    return json.loads(raw)


def register_agent(
    registration_key: str,
    name: str,
    description: str,
    *,
    base_url: Optional[str] = None,
    display_name: Optional[str] = None,
    connection_model: str = "always_on",
    overwrite: bool = False,
    save: bool = True,
) -> Dict[str, str]:
    """Register a custom agent with `POST /agents`. Returns {id, api_key}.

    The API key is returned ONCE; with `save=True` it is stored in
    ~/.airlock/agents/<name>.json (mode 600). `overwrite=True` re-registers
    an existing name you own and rotates its key. Names must match
    ^[a-z0-9][a-z0-9._-]*$.
    """
    base_url = (base_url or default_base_url()).rstrip("/")
    body: Dict[str, Any] = {
        "name": name,
        "description": description,
        "connector_type": "external",
        "integration_profile": {
            "connection_model": connection_model,
            "message_exchange": True,
            "pre_invocation_mediation": [],
            "post_invocation_mediation": [],
            "event_reporting": [],
        },
        "overwrite": overwrite,
    }
    if display_name:
        body["display_name"] = display_name
    result = _http(base_url, "POST", "/agents", registration_key, body)
    if save:
        save_credentials(name, result["id"], result["api_key"], base_url)
    return {"id": result["id"], "api_key": result["api_key"]}


# --------------------------------------------------------------------------- #
# Minimal WebSocket client (RFC 6455, client side)
# --------------------------------------------------------------------------- #

_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class _WSClosed(Exception):
    def __init__(self, code: Optional[int] = None, reason: str = ""):
        self.code = code
        self.reason = reason
        super().__init__(f"websocket closed ({code}) {reason}")


class _WebSocket:
    def __init__(self, url: str, headers: Dict[str, str], timeout: float = 15.0):
        parts = urllib.parse.urlsplit(url)
        secure = parts.scheme in ("wss", "https")
        host = parts.hostname or "localhost"
        port = parts.port or (443 if secure else 80)
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        sock = socket.create_connection((host, port), timeout=timeout)
        if secure:
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
        self._sock = sock
        self._buf = b""
        self._send_lock = threading.Lock()
        key = base64.b64encode(os.urandom(16)).decode()
        host_hdr = host if parts.port is None else f"{host}:{port}"
        lines = [
            f"GET {path} HTTP/1.1",
            f"Host: {host_hdr}",
            "Upgrade: websocket",
            "Connection: Upgrade",
            f"Sec-WebSocket-Key: {key}",
            "Sec-WebSocket-Version: 13",
        ] + [f"{k}: {v}" for k, v in headers.items()]
        sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
        head = self._read_until(b"\r\n\r\n")
        status_line, _, rest = head.decode("latin-1").partition("\r\n")
        try:
            status = int(status_line.split()[1])
        except (IndexError, ValueError):
            raise SwitchError(f"bad handshake response: {status_line!r}") from None
        resp_headers = {}
        for line in rest.split("\r\n"):
            if ":" in line:
                k, v = line.split(":", 1)
                resp_headers[k.strip().lower()] = v.strip()
        if status != 101:
            body = self._buf.decode(errors="replace")[:300]
            self.close_socket()
            raise ConnectionRefused(
                f"websocket upgrade refused: HTTP {status} {body}", status=status
            )
        expected = base64.b64encode(
            hashlib.sha1((key + _WS_GUID).encode()).digest()
        ).decode()
        if resp_headers.get("sec-websocket-accept") != expected:
            self.close_socket()
            raise SwitchError("websocket handshake: bad Sec-WebSocket-Accept")

    def _read_until(self, marker: bytes) -> bytes:
        while marker not in self._buf:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise _WSClosed(None, "eof during handshake")
            self._buf += chunk
        head, _, self._buf = self._buf.partition(marker)
        return head

    def _read_exact(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self._sock.recv(max(4096, n - len(self._buf)))
            if not chunk:
                raise _WSClosed(None, "eof")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def settimeout(self, t: Optional[float]) -> None:
        self._sock.settimeout(t)

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        header = bytes([0x80 | opcode])
        n = len(payload)
        if n < 126:
            header += bytes([0x80 | n])
        elif n < 65536:
            header += bytes([0x80 | 126]) + struct.pack("!H", n)
        else:
            header += bytes([0x80 | 127]) + struct.pack("!Q", n)
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        with self._send_lock:
            self._sock.sendall(header + mask + masked)

    def send_text(self, text: str) -> None:
        self._send_frame(0x1, text.encode())

    def recv_text(self) -> str:
        """Next text message. Answers protocol pings; raises _WSClosed on close."""
        fragments: List[bytes] = []
        while True:
            b1, b2 = self._read_exact(2)
            fin, opcode = b1 & 0x80, b1 & 0x0F
            n = b2 & 0x7F
            if n == 126:
                n = struct.unpack("!H", self._read_exact(2))[0]
            elif n == 127:
                n = struct.unpack("!Q", self._read_exact(8))[0]
            mask = self._read_exact(4) if b2 & 0x80 else None
            payload = self._read_exact(n)
            if mask:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if opcode == 0x8:  # close
                code = struct.unpack("!H", payload[:2])[0] if len(payload) >= 2 else None
                reason = payload[2:].decode(errors="replace")
                try:
                    self._send_frame(0x8, payload[:2])
                except OSError:
                    pass
                raise _WSClosed(code, reason)
            if opcode == 0x9:  # transport-level ping
                self._send_frame(0xA, payload)
                continue
            if opcode == 0xA:
                continue
            if opcode in (0x1, 0x2, 0x0):
                fragments.append(payload)
                if fin:
                    return b"".join(fragments).decode()

    def close(self, code: int = 1000) -> None:
        try:
            self._send_frame(0x8, struct.pack("!H", code))
        except OSError:
            pass
        self.close_socket()

    def close_socket(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass


# --------------------------------------------------------------------------- #
# Agent
# --------------------------------------------------------------------------- #


@dataclass
class Message:
    """One inbound room message."""

    room_id: str
    sender: str  # participant id, e.g. "@switch-user-ab12:server" (opaque)
    sender_name: str
    text: str
    mentioned: bool  # payload.addressed: the agent was @mentioned / addressed
    message_id: str
    thread_id: Optional[str]  # root of the thread it was posted in, if any
    timestamp: Optional[int]  # epoch ms
    attachments: List[Dict[str, Any]] = field(default_factory=list)
    gap: Optional[Dict[str, Any]] = None  # set when events were lost before this one
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def reply_thread_id(self) -> str:
        """The thread to reply into: the existing thread, or start one on this message."""
        return self.thread_id or self.message_id


class SwitchAgent:
    """A registered agent: post messages, run operations, listen for messages.

    >>> agent = SwitchAgent.from_name("airlock-echo")
    >>> room = agent.find_room("airlock-demo")
    >>> for msg in agent.listen([room]):
    ...     if msg.mentioned:
    ...         agent.reply(msg, "echo: " + msg.text)
    """

    HEARTBEAT_STALL_SECONDS = 10.0  # no frame for this long -> reconnect

    def __init__(self, agent_id: str, api_key: str, base_url: Optional[str] = None, name: Optional[str] = None):
        self.agent_id = agent_id
        self.name = name
        self.base_url = (base_url or default_base_url()).rstrip("/")
        self._api_key = api_key
        self.connection_id = str(uuid.uuid4())  # reused across reconnects
        self.cursor: Optional[int] = None
        self.rooms: List[str] = []  # server's view, from connection_state
        self.connection_state: Dict[str, Any] = {}
        self._sent_ids: set = set()
        self._own_sender: Optional[str] = None
        self._stop = threading.Event()
        self._ws: Optional[_WebSocket] = None

    def __repr__(self) -> str:  # never show the key
        return f"SwitchAgent(name={self.name!r}, agent_id={self.agent_id!r}, base_url={self.base_url!r})"

    @classmethod
    def from_name(cls, name: str, base_url: Optional[str] = None) -> "SwitchAgent":
        creds = load_credentials(name)
        return cls(
            creds["agent_id"],
            creds["api_key"],
            base_url or os.environ.get("SWITCH_BASE_URL") or creds.get("base_url"),
            name=name,
        )

    # -- HTTP ---------------------------------------------------------------

    def _call(self, method: str, path: str, body: Any = None, headers: Optional[Dict[str, str]] = None) -> Any:
        return _http(self.base_url, method, path, self._api_key, body, headers)

    def operations(self) -> Dict[str, Any]:
        """Every operation the server offers, {name: {description, input_schema}}."""
        return self._call("GET", f"/agents/{self.agent_id}/ops")["operations"]

    def op(self, name: str, *, bind_connection: bool = True, **arguments: Any) -> Any:
        """Run one operation. Arguments are sent unwrapped; returns `result`.

        With `bind_connection`, the call carries X-Switch-Connection-Id so
        room-bound operations (post_message, read_context, ...) resolve the
        room this connection holds. It needs a live connection (listen()).
        """
        headers = {}
        if bind_connection and self.connection_state:
            headers["X-Switch-Connection-Id"] = self.connection_id
        resp = self._call(
            "POST", f"/agents/{self.agent_id}/ops/{name}", arguments, headers
        )
        return resp.get("result") if isinstance(resp, dict) else resp

    # -- rooms ----------------------------------------------------------------

    def list_rooms(self, include_archived: bool = False) -> List[Dict[str, Any]]:
        """Rooms this agent is a member of: [{room_id, name, description, connected, archived}]."""
        return self.op("list_rooms", bind_connection=False, include_archived=include_archived)

    def list_all_rooms(self, include_archived: bool = False) -> List[Dict[str, Any]]:
        """Every room on the instance (membership not required to see them)."""
        return self.op("list_all_rooms", bind_connection=False, include_archived=include_archived)

    def find_room(self, name: str, *, all_rooms: bool = False) -> str:
        """Room id by name: exact match (case-insensitive), else a unique substring."""
        rooms = self.list_all_rooms() if all_rooms else self.list_rooms()
        wanted = name.strip().lower()
        exact = [r for r in rooms if (r.get("name") or "").lower() == wanted]
        if len(exact) == 1:
            return exact[0]["room_id"]
        partial = [r for r in rooms if wanted in (r.get("name") or "").lower()]
        if len(exact) > 1 or len(partial) > 1:
            names = ", ".join(sorted(r["name"] for r in (exact or partial)))
            raise SwitchError(f"room name {name!r} is ambiguous: {names}")
        if len(partial) == 1:
            return partial[0]["room_id"]
        where = "on this server" if all_rooms else "that this agent belongs to"
        raise SwitchError(
            f"no room named {name!r} {where}. Add the agent to the room in the "
            "Switch dashboard (or via add/invite) first."
        )

    # -- sending ----------------------------------------------------------------

    def send(self, room_id: str, text: str) -> Optional[str]:
        """Post a top-level message. Needs no connection. Returns the event id."""
        resp = self._call(
            "POST",
            f"/agents/{self.agent_id}/message",
            {"room_id": room_id, "content": text},
        )
        event_id = resp.get("event_id") if isinstance(resp, dict) else None
        if event_id:
            self._sent_ids.add(event_id)
        return event_id

    def reply(self, target: Any, text: str, thread_id: Optional[str] = None) -> Optional[str]:
        """Reply to a Message in its thread, or post to a room id.

        Threaded replies use the `post_message` operation, which posts to the
        room the connection holds, so they work when the live connection
        covers exactly one room (the common case: listen([room])). Otherwise
        this falls back to a top-level post via `send`.
        """
        if isinstance(target, Message):
            room_id = target.room_id
            thread_id = thread_id or target.reply_thread_id
        else:
            room_id = str(target)
        if thread_id and self.connection_state and self.rooms == [room_id]:
            result = self.op("post_message", body=text, thread_id=thread_id)
            event_id = result.get("event_id") if isinstance(result, dict) else None
            if event_id:
                self._sent_ids.add(event_id)
            return event_id
        return self.send(room_id, text)

    # -- receiving --------------------------------------------------------------

    def stop(self) -> None:
        """Ask listen() to end (safe from another thread or a signal handler)."""
        self._stop.set()
        ws = self._ws
        if ws is not None:
            ws.close_socket()

    def listen(
        self,
        rooms: Optional[List[str]] = None,
        *,
        only_mentions: bool = False,
        include_own: bool = False,
        scope: Optional[str] = None,
        on_event: Any = None,
    ) -> Iterator[Message]:
        """Open the WebSocket connection and yield inbound room messages.

        `rooms`: room ids to claim on open (declared on the URL so catch-up
        covers them). One room -> scope "single"; several or none -> "all".
        `only_mentions`: server-side filter=addressed (only messages that
        address this agent). Otherwise every message arrives, with
        `mentioned` telling you whether it addressed the agent.
        `on_event(event_name, data)`: optional hook for every non-message frame.

        Pings are answered on a background thread, so a slow handler in the
        loop body does not lapse the 6-second heartbeat. Reconnects reuse the
        connection id and resume from the last cursor.
        """
        rooms = list(rooms or [])
        if scope is None:
            scope = "single" if len(rooms) == 1 else "all"
        inbox: "queue.Queue[Any]" = queue.Queue()
        self._stop.clear()
        reader = threading.Thread(
            target=self._run,
            args=(rooms, scope, "addressed" if only_mentions else "all", inbox),
            name=f"switch-agent-{self.agent_id[:8]}",
            daemon=True,
        )
        reader.start()
        pending_gap: Optional[Dict[str, Any]] = None
        try:
            while True:
                try:
                    item = inbox.get(timeout=0.5)
                except queue.Empty:
                    if not reader.is_alive():
                        return
                    continue
                if isinstance(item, BaseException):
                    raise item
                if item is None:
                    return
                event, data = item
                if event == "message":
                    msg = self._to_message(data)
                    if msg is None:
                        continue
                    if not include_own and self._is_own(msg):
                        continue
                    msg.gap, pending_gap = pending_gap, None
                    yield msg
                else:
                    if event == "gap":
                        # A gap is not a wake: attach it to the next message.
                        pending_gap = data
                    if on_event is not None:
                        on_event(event, data)
        finally:
            self.stop()
            reader.join(timeout=3)

    def _to_message(self, data: Dict[str, Any]) -> Optional[Message]:
        p = data.get("payload") or {}
        if not p:
            return None
        return Message(
            room_id=data.get("room_id", ""),
            sender=p.get("sender", ""),
            sender_name=p.get("sender_name", ""),
            text=p.get("body", ""),
            mentioned=bool(p.get("addressed")),
            message_id=p.get("message_id", ""),
            thread_id=p.get("thread_id"),
            timestamp=p.get("timestamp"),
            attachments=list(p.get("attachments") or []),
            raw=data,
        )

    def _is_own(self, msg: Message) -> bool:
        if msg.message_id in self._sent_ids:
            self._own_sender = msg.sender or self._own_sender
            return True
        return bool(self._own_sender) and msg.sender == self._own_sender

    def _ws_url(self, rooms: List[str], scope: str, flt: str) -> str:
        base = self.base_url
        if base.startswith("https://"):
            base = "wss://" + base[len("https://"):]
        elif base.startswith("http://"):
            base = "ws://" + base[len("http://"):]
        params = {
            "connection_id": self.connection_id,
            "scope": scope,
            "filter": flt,
            "start_from": "head" if self.cursor is None else str(self.cursor),
            "client": CLIENT_NAME,
            "client_version": CLIENT_VERSION,
        }
        if rooms:
            params["rooms"] = ",".join(rooms)
        return f"{base}/agents/{self.agent_id}/connection/ws?{urllib.parse.urlencode(params)}"

    def _run(self, rooms: List[str], scope: str, flt: str, inbox: "queue.Queue[Any]") -> None:
        backoff = 0.5
        try:
            while not self._stop.is_set():
                try:
                    self._session(rooms, scope, flt, inbox)
                    backoff = 0.5  # a clean session resets the backoff
                except ConnectionRefused as exc:
                    # 401/403: wrong key or not a room member; taken_over: another
                    # process owns this connection id. Retrying will not help.
                    if exc.status in (400, 401, 403, 404) or exc.code == "taken_over":
                        inbox.put(exc)
                        return
                except (OSError, _WSClosed, ValueError, SwitchError):
                    pass
                finally:
                    self.connection_state = {}
                if self._stop.is_set():
                    break
                time.sleep(backoff)
                backoff = min(backoff * 2, 10.0)
        finally:
            inbox.put(None)

    def _session(self, rooms: List[str], scope: str, flt: str, inbox: "queue.Queue[Any]") -> None:
        ws = _WebSocket(
            self._ws_url(rooms, scope, flt),
            {"Authorization": f"Bearer {self._api_key}"},
        )
        self._ws = ws
        try:
            ws.settimeout(self.HEARTBEAT_STALL_SECONDS)
            while not self._stop.is_set():
                try:
                    raw = ws.recv_text()
                except socket.timeout:
                    return  # server pings every 2 s; silence means a dead link
                except _WSClosed as closed:
                    if closed.code and closed.code >= 4000:
                        raise ConnectionRefused(
                            f"connection closed by server: {closed.code} {closed.reason}",
                            status=closed.code - 4000,
                        ) from None
                    return
                frame = json.loads(raw)
                event = frame.get("event")
                data = frame.get("data") or {}
                if "id" in frame and frame["id"] is not None:
                    self.cursor = frame["id"]
                if event == "ping":
                    ws.send_text(json.dumps({"type": "pong", "cursor": self.cursor or 0}))
                    continue
                if event == "connection_state":
                    self.connection_state = data
                    self.rooms = list(data.get("rooms") or [])
                    if self.cursor is None and isinstance(data.get("cursor"), int):
                        self.cursor = data["cursor"]
                elif event == "subscription_changed":
                    self.rooms = list(data.get("rooms") or [])
                elif event == "gap":
                    if isinstance(data.get("resumed_at"), int):
                        self.cursor = data["resumed_at"]
                elif event == "refused":
                    raise ConnectionRefused(
                        f"connection refused: {data.get('status')} {data.get('detail')}",
                        status=data.get("status"),
                    )
                elif event == "evicted":
                    code = data.get("code")
                    if code == "taken_over":
                        raise ConnectionRefused(
                            "another client took over this connection id", code=code
                        )
                    inbox.put((event, data))
                    return  # reconnect
                inbox.put((event, data))
        finally:
            self._ws = None
            ws.close()


# --------------------------------------------------------------------------- #
# CLI: register / import / rooms
# --------------------------------------------------------------------------- #


def _main(argv: Optional[List[str]] = None) -> int:
    import argparse
    import getpass

    ap = argparse.ArgumentParser(description="Switch custom agent helper")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("register", help="register an agent with a registration key")
    r.add_argument("name")
    r.add_argument("--description", default="Airlock custom agent")
    r.add_argument("--display-name")
    r.add_argument("--overwrite", action="store_true", help="re-register and rotate the key")
    r.add_argument("--base-url")

    i = sub.add_parser("import", help="save an agent id + API key made in the dashboard")
    i.add_argument("name")
    i.add_argument("agent_id")
    i.add_argument("--base-url")

    lr = sub.add_parser("rooms", help="list rooms the agent belongs to")
    lr.add_argument("name")
    lr.add_argument("--all", action="store_true", help="every room on the server")

    args = ap.parse_args(argv)
    if args.cmd == "register":
        key = os.environ.get("SWITCH_REGISTRATION_KEY") or getpass.getpass(
            "Registration key (input hidden): "
        )
        res = register_agent(
            key.strip(),
            args.name,
            args.description,
            base_url=args.base_url,
            display_name=args.display_name,
            overwrite=args.overwrite,
        )
        print(f"registered {args.name}: id={res['id']}; key saved to {credentials_path(args.name)}")
    elif args.cmd == "import":
        api_key = getpass.getpass("Agent API key (input hidden): ").strip()
        path = save_credentials(args.name, args.agent_id, api_key, args.base_url or default_base_url())
        print(f"saved {path}")
    elif args.cmd == "rooms":
        agent = SwitchAgent.from_name(args.name)
        rooms = agent.list_all_rooms() if args.all else agent.list_rooms()
        for room in rooms:
            print(f"{room['room_id']}  {room['name']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
