"""The ttyd wire protocol, with no Qt in it.

ttyd runs inside each element's container and owns the real PTY; this module says what to put on
the WebSocket and how to read what comes back. Everything here is a pure function over bytes, for
two reasons: the protocol is the part most likely to be subtly wrong, and QtWebSockets ships in
PySide6-Addons — so a Qt-coupled implementation could not be tested where Addons is absent.

THE PROTOCOL (ttyd 1.7.x)

  handshake   GET /token -> {"token": "..."}. With no credentials configured ttyd still answers,
              often with an empty string; empty is normal, not a failure.

              The first WebSocket message is JSON with no command byte:
                  {"AuthToken": "<token>", "columns": <n>, "rows": <n>}
              The socket is opened with the "tty" subprotocol.

  client ->   one command byte, then the payload:
                  '0' + data          INPUT — keystrokes, already encoded
                  '1' + JSON          RESIZE_TERMINAL {"columns": n, "rows": n}
                  '2'                 PAUSE
                  '3'                 RESUME

  server ->   same shape:
                  '0' + data          OUTPUT — feed straight to the emulator
                  '1' + title         SET_WINDOW_TITLE
                  '2' + JSON          SET_PREFERENCES

Payloads are bytes, not str: terminal output is a byte stream that may split a UTF-8 sequence
across frames, so decoding belongs downstream in the emulator where the partial-sequence state
lives, never here.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

# client -> server
INPUT = b"0"
RESIZE = b"1"
PAUSE = b"2"
RESUME = b"3"

# server -> client
OUTPUT = b"0"
SET_TITLE = b"1"
SET_PREFERENCES = b"2"

SUBPROTOCOL = "tty"
TOKEN_PATH = "/token"
WS_PATH = "/ws"


@dataclass(frozen=True)
class Frame:
    """One decoded server message. `kind` is the raw command byte."""

    kind: bytes
    payload: bytes

    @property
    def is_output(self) -> bool:
        return self.kind == OUTPUT

    @property
    def title(self) -> str:
        return self.payload.decode("utf-8", "replace") if self.kind == SET_TITLE else ""


def auth_message(token: str, columns: int, rows: int) -> bytes:
    """The first frame. Note it carries the initial size, so the shell starts at the right
    geometry instead of at 80x24 and reflowing on the first resize."""
    return json.dumps({"AuthToken": token or "",
                       "columns": int(columns),
                       "rows": int(rows)}).encode("utf-8")


def encode_input(data: bytes) -> bytes:
    """Keystrokes, already encoded by the key mapper."""
    return INPUT + data


def encode_resize(columns: int, rows: int) -> bytes:
    """A resize the PTY will see as SIGWINCH. Both values are clamped to at least 1: a pane
    dragged shut reports 0 columns, and a 0-column PTY makes some programs divide by zero."""
    return RESIZE + json.dumps({"columns": max(1, int(columns)),
                                "rows": max(1, int(rows))}).encode("utf-8")


def decode(message: bytes) -> Frame | None:
    """Split a server frame. None for an empty message, which some proxies send as a keepalive."""
    if not message:
        return None
    return Frame(kind=message[:1], payload=message[1:])


def token_from(body: bytes) -> str:
    """Pull the token out of GET /token.

    Tolerant on purpose: an unauthenticated ttyd may answer `{}`, an empty body, or a bare string.
    None of those is an error — the token is simply empty, and the handshake still succeeds. Only
    a malformed body reaches the caller as an empty token, which fails the same harmless way.
    """
    if not body:
        return ""
    try:
        data = json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        return ""
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        return str(data.get("token", "") or "")
    return ""


def token_url(base: str) -> str:
    return base.rstrip("/") + TOKEN_PATH


def ws_url(base: str) -> str:
    """http://host:port/ -> ws://host:port/ws (and https -> wss, for symmetry)."""
    b = base.rstrip("/")
    if b.startswith("https://"):
        b = "wss://" + b[len("https://"):]
    elif b.startswith("http://"):
        b = "ws://" + b[len("http://"):]
    return b + WS_PATH
