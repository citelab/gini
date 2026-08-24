"""WebSocket client for the ttyd server inside an element's container.

Thin on purpose: every decision about what the bytes MEAN lives in ttyd_protocol.py, which has no
Qt in it and is therefore testable anywhere. This file is the wiring — fetch the token, open the
socket, pass bytes both ways.

Why ttyd stays even though the web view is gone: the PTY lives inside Linux, in the container. No
pseudo-terminal code runs on the host at all, so macOS, Linux and Windows students get identical
behaviour, and `docker exec -t` (which insists on a real TTY on the host side) never comes into
it. What we dropped was Chromium, not the transport.

Set GINI_TTYD_DEBUG=1 to log the handshake and the first frames. The protocol is the part most
likely to be subtly wrong against a given ttyd build, and a mismatch is otherwise invisible: the
terminal simply stays blank.
"""
from __future__ import annotations

import os

from PySide6.QtCore import QObject, QUrl, Signal

from . import ttyd_protocol as proto


class TtydClient(QObject):
    """One connection to one element's terminal."""

    output = Signal(bytes)        # terminal output, straight to the emulator
    connected = Signal()
    closed = Signal()
    failed = Signal(str)          # human-readable; shown in the panel's subtitle
    title = Signal(str)           # ttyd's SET_WINDOW_TITLE

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._sock = None
        self._net = None
        self._reply = None
        self._base = ""
        self._cols, self._rows = 80, 24
        self._debug = os.environ.get("GINI_TTYD_DEBUG") == "1"

    # -- lifecycle ----------------------------------------------------------- #
    def connect_to(self, base_url: str, columns: int, rows: int) -> None:
        """Point at http://127.0.0.1:<port>/ and open a session."""
        self.disconnect_from()
        self._base = base_url
        self._cols, self._rows = max(1, columns), max(1, rows)
        from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest
        self._net = QNetworkAccessManager(self)
        req = QNetworkRequest(QUrl(proto.token_url(base_url)))
        self._reply = self._net.get(req)
        self._reply.finished.connect(self._on_token)

    def disconnect_from(self) -> None:
        if self._reply is not None:
            self._reply.abort()
            self._reply.deleteLater()
            self._reply = None
        if self._sock is not None:
            sock, self._sock = self._sock, None
            try:
                sock.close()
            except RuntimeError:
                pass
            sock.deleteLater()

    def is_open(self) -> bool:
        return self._sock is not None

    # -- handshake ----------------------------------------------------------- #
    def _on_token(self) -> None:
        reply, self._reply = self._reply, None
        if reply is None:
            return
        body = bytes(reply.readAll().data())
        err = reply.error()
        reply.deleteLater()
        # A token endpoint that errors is NOT fatal: an unauthenticated ttyd answers oddly on some
        # builds, and the handshake carries an empty token perfectly well. Only the socket failing
        # is a real failure, and that path reports itself.
        token = proto.token_from(body)
        if self._debug:
            print(f"[ttyd] token url={proto.token_url(self._base)} err={err} "
                  f"body={body[:120]!r} -> token={token!r}")
        self._open_socket(token)

    def _open_socket(self, token: str) -> None:
        try:
            from PySide6.QtWebSockets import QWebSocket, QWebSocketHandshakeOptions
        except ImportError as e:            # QtWebSockets lives in PySide6-Addons
            self.failed.emit(f"terminal needs PySide6-Addons (QtWebSockets): {e}")
            return
        from PySide6.QtNetwork import QNetworkRequest
        self._sock = QWebSocket()
        self._sock.binaryMessageReceived.connect(self._on_binary)
        self._sock.textMessageReceived.connect(lambda s: self._on_binary(s.encode("utf-8")))
        self._sock.connected.connect(lambda: self._on_connected(token))
        self._sock.disconnected.connect(self._on_disconnected)
        self._sock.errorOccurred.connect(
            lambda _e: self.failed.emit(self._sock.errorString() if self._sock else "socket error"))
        opts = QWebSocketHandshakeOptions()
        opts.setSubprotocols([proto.SUBPROTOCOL])
        url = proto.ws_url(self._base)
        if self._debug:
            print(f"[ttyd] opening {url} subprotocol={proto.SUBPROTOCOL}")
        self._sock.open(QNetworkRequest(QUrl(url)), opts)

    def _on_connected(self, token: str) -> None:
        # Text frame, and no command byte — the auth message is the one exception to the framing.
        # It also carries the initial geometry, so the shell starts the right size rather than at
        # 80x24 and reflowing on the first resize.
        msg = proto.auth_message(token, self._cols, self._rows)
        if self._debug:
            print(f"[ttyd] auth {msg!r}")
        self._sock.sendTextMessage(msg.decode("utf-8"))
        self.connected.emit()

    def _on_disconnected(self) -> None:
        self._sock = None
        self.closed.emit()

    # -- traffic -------------------------------------------------------------- #
    def _on_binary(self, message) -> None:
        frame = proto.decode(bytes(message))
        if frame is None:
            return
        if frame.is_output:
            self.output.emit(frame.payload)
        elif frame.kind == proto.SET_TITLE:
            self.title.emit(frame.title)
        elif self._debug:
            print(f"[ttyd] frame kind={frame.kind!r} payload={frame.payload[:80]!r}")

    def send_input(self, data: bytes) -> None:
        if self._sock is not None and data:
            self._sock.sendBinaryMessage(proto.encode_input(data))

    def send_resize(self, columns: int, rows: int) -> None:
        self._cols, self._rows = max(1, columns), max(1, rows)
        if self._sock is not None:
            self._sock.sendBinaryMessage(proto.encode_resize(columns, rows))
