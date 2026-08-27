"""The ttyd wire protocol.

This is the layer most likely to be subtly wrong, and its failure mode is the worst kind: the
terminal simply stays blank, with nothing in any log to say why. It is also the layer we cannot
verify against a real ttyd from a test — so the tests below pin the contract precisely as
documented, and ttyd_client sets GINI_TTYD_DEBUG=1 to print the handshake when reality disagrees.

Deliberately Qt-free: QtWebSockets ships in PySide6-Addons, so a Qt-coupled protocol could not be
tested at all on an Essentials-only install.
"""
import json

from gini.services import ttyd_protocol as proto


def test_the_auth_message_carries_the_initial_size():
    """Sending the geometry up front means the shell starts at the right size instead of at 80x24
    and reflowing — which a student sees as the first command wrapping oddly."""
    msg = json.loads(proto.auth_message("tok", 120, 40))
    assert msg == {"AuthToken": "tok", "columns": 120, "rows": 40}


def test_the_auth_message_has_no_command_byte():
    """The one exception to the framing. Prefixing it makes ttyd reject the handshake."""
    raw = proto.auth_message("tok", 80, 24)
    assert raw.startswith(b"{"), f"auth frame is prefixed: {raw[:8]!r}"


def test_an_empty_token_is_normal():
    """ttyd with no credentials configured answers with an empty token, or nothing at all. That
    is not an error and must still produce a valid handshake."""
    assert json.loads(proto.auth_message("", 80, 24))["AuthToken"] == ""


def test_input_is_prefixed_with_the_input_command():
    assert proto.encode_input(b"ls\r") == b"0ls\r"


def test_input_carries_control_bytes_untouched():
    """Ctrl-C is the whole point of having a terminal rather than a log pane."""
    assert proto.encode_input(b"\x03") == b"0\x03"


def test_resize_is_json_after_the_command_byte():
    raw = proto.encode_resize(100, 30)
    assert raw[:1] == proto.RESIZE
    assert json.loads(raw[1:]) == {"columns": 100, "rows": 30}


def test_resize_never_reports_zero():
    """A pane dragged shut reports 0 columns, and a 0-column PTY makes some programs divide by
    zero — `less` and `top` among them."""
    assert json.loads(proto.encode_resize(0, 0)[1:]) == {"columns": 1, "rows": 1}


def test_output_frames_decode_to_raw_bytes():
    f = proto.decode(b"0hello")
    assert f.is_output and f.payload == b"hello"


def test_payloads_stay_bytes_so_split_utf8_survives():
    """A UTF-8 sequence can be split across frames. Decoding here would corrupt it; the emulator
    downstream is where the partial-sequence state lives."""
    first = proto.decode(b"0" + "é".encode()[:1])
    second = proto.decode(b"0" + "é".encode()[1:])
    assert isinstance(first.payload, bytes) and isinstance(second.payload, bytes)
    assert (first.payload + second.payload).decode() == "é"


def test_a_title_frame_is_not_mistaken_for_output():
    """Feeding a window title into the emulator prints it into the student's shell."""
    f = proto.decode(b"1r1: /bin/sh")
    assert not f.is_output
    assert f.title == "r1: /bin/sh"


def test_an_empty_message_is_ignored():
    """Some proxies send empty frames as keepalives."""
    assert proto.decode(b"") is None


def test_token_parsing_tolerates_what_ttyd_actually_returns():
    assert proto.token_from(b'{"token": "abc"}') == "abc"
    assert proto.token_from(b"{}") == ""              # unauthenticated
    assert proto.token_from(b"") == ""                # empty body
    assert proto.token_from(b'"abc"') == "abc"        # bare string
    assert proto.token_from(b"not json") == ""        # malformed: empty, not an exception


def test_urls():
    assert proto.token_url("http://127.0.0.1:37600/") == "http://127.0.0.1:37600/token"
    assert proto.ws_url("http://127.0.0.1:37600/") == "ws://127.0.0.1:37600/ws"
    assert proto.ws_url("https://h:1") == "wss://h:1/ws"


def test_client_and_server_command_bytes_do_not_collide():
    """Both directions use '0' for data and '1' for a control message, which is easy to get
    backwards when reading the code. Pin the values so a 'tidy-up' cannot renumber them."""
    assert (proto.INPUT, proto.RESIZE) == (b"0", b"1")
    assert (proto.OUTPUT, proto.SET_TITLE, proto.SET_PREFERENCES) == (b"0", b"1", b"2")
    assert proto.SUBPROTOCOL == "tty"
