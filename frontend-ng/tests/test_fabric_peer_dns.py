"""A machine finds its fabric peer by asking DNS, never the lab's hosts file.

Found on a three-machine, one-router lab: one machine could send nothing at all for the whole run.
Its shuttle had looked up its peer, the container `r1`, AFTER gBuilder wrote the overlay names into
/etc/hosts (`10.0.1.1  R1  R1-eth0 …`). Hosts lookups ignore case and come before DNS, so `r1` was
10.0.1.1 — an address inside the lab — and every frame went back into the machine's own tap. Read
inside the real container: `gethostbyname('r1')` -> 10.0.1.1, the router's container 172.18.0.3.
Whether a machine lost depended on whether its first frame beat the write by half a second.
"""
from __future__ import annotations

import socket
import struct
import threading
import time

import pytest

from gini.runtime import transport as tp


# ---- a DNS server small enough to read ------------------------------------------------------- #
@pytest.fixture
def dns():
    """Answers A queries from `table` on a loopback port; an unknown name gets no answers."""
    table = {"r1": "172.18.0.3"}
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    s.settimeout(0.2)
    stop = threading.Event()

    def serve():
        while not stop.is_set():
            try:
                q, addr = s.recvfrom(512)
            except OSError:
                continue
            qid = q[:2]
            i, labels = 12, []
            while q[i]:
                labels.append(q[i + 1:i + 1 + q[i]].decode())
                i += 1 + q[i]
            question = q[12:i + 5]
            ip = table.get(".".join(labels).lower())
            head = qid + struct.pack(">HHHHH", 0x8180, 1, 1 if ip else 0, 0, 0)
            answer = (b"\xc0\x0c" + struct.pack(">HHIH", 1, 1, 60, 4) + socket.inet_aton(ip)
                      if ip else b"")                   # name by pointer, as real servers do
            s.sendto(head + question + answer, addr)

    th = threading.Thread(target=serve, daemon=True)
    th.start()
    yield s.getsockname()[1], table
    stop.set()
    th.join(1)
    s.close()


def test_the_answer_comes_from_the_dns_server(dns):
    port, _ = dns
    assert tp.ask_dns("r1", "127.0.0.1", port=port) == "172.18.0.3"


def test_a_name_dns_does_not_know_is_none_not_an_error(dns):
    port, _ = dns
    assert tp.ask_dns("m9", "127.0.0.1", port=port) is None


def test_no_dns_server_there_is_an_oserror():
    """So the caller can tell "no such name" from "no DNS at all"."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()                                           # nothing listens there now
    with pytest.raises(OSError):
        tp.ask_dns("r1", "127.0.0.1", port=port, timeout=0.2)


# ---- the decision ------------------------------------------------------------------------------ #
def test_the_hosts_file_never_answers_for_a_container(monkeypatch):
    """THE bug: the lab's `R1` line must not answer for the container `r1`."""
    monkeypatch.setattr(tp.socket, "gethostbyname", lambda h: "10.0.1.1")   # the hosts file
    monkeypatch.setattr(tp, "ask_dns", lambda h, srv, **k: "172.18.0.3")
    assert tp.resolve_peer("r1", servers=["127.0.0.11"]) == "172.18.0.3"


def test_dns_that_does_not_know_the_peer_yet_means_ask_again_not_ask_the_hosts_file(monkeypatch):
    """Compose starts containers in any order; a peer not registered YET must not be looked up
    somewhere that will answer with the wrong thing."""
    monkeypatch.setattr(tp.socket, "gethostbyname", lambda h: "10.0.1.1")
    monkeypatch.setattr(tp, "ask_dns", lambda h, srv, **k: None)
    assert tp.resolve_peer("r1", servers=["127.0.0.11"]) is None


def test_without_any_dns_server_the_ordinary_lookup_is_used(monkeypatch):
    """Not a container: nothing wrote a lab block, and there is no other way to ask."""
    monkeypatch.setattr(tp.socket, "gethostbyname", lambda h: "192.0.2.7")
    assert tp.resolve_peer("somehost", servers=[]) == "192.0.2.7"


def test_an_address_is_its_own_answer(monkeypatch):
    monkeypatch.setattr(tp, "ask_dns", lambda *a, **k: pytest.fail("asked DNS for an address"))
    assert tp.resolve_peer("127.0.0.1", servers=["127.0.0.11"]) == "127.0.0.1"


def test_nameservers_are_read_from_resolv_conf(tmp_path):
    f = tmp_path / "resolv.conf"
    f.write_text("search lan\nnameserver 127.0.0.11\noptions ndots:0\nnameserver 10.89.0.1\n")
    assert tp._nameservers(str(f)) == ["127.0.0.11", "10.89.0.1"]
    assert tp._nameservers(str(tmp_path / "missing")) == []


# ---- the port -------------------------------------------------------------------------------- #
def test_a_port_learns_its_peer_from_what_the_peer_sends():
    """The safety net, as the gRouter has (tun.c): whatever the lookup said, a datagram from the
    peer's port is the peer at its real address — and replies go there from then on."""
    b = tp.Port(0, "127.0.0.1", 9, bind_host="127.0.0.1")
    b_port = b.sock.getsockname()[1]
    a = tp.Port(0, "192.0.2.99", b_port, bind_host="127.0.0.1")      # wrong address to start
    a._peer_ip = "192.0.2.99"
    b.peer_port = a.sock.getsockname()[1]
    b.send(b"hello")
    deadline = time.monotonic() + 2
    while a.recv() is None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert a._peer_ip == "127.0.0.1"
    a.send(b"back")
    deadline, got = time.monotonic() + 2, None
    while got is None and time.monotonic() < deadline:
        got = b.recv()
        time.sleep(0.01)
    assert got == b"back"
    a.sock.close()
    b.sock.close()


def test_a_datagram_from_another_port_does_not_move_the_peer():
    a = tp.Port(0, "127.0.0.1", 1, bind_host="127.0.0.1")
    other = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    other.bind(("127.0.0.1", 0))
    a._peer_ip = "127.0.0.1"
    a.peer_port = 1
    other.sendto(b"x", a.sock.getsockname())
    deadline = time.monotonic() + 2
    while a.recv() is None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert a._peer_ip == "127.0.0.1" and a.peer_port == 1
    a.sock.close()
    other.close()


def test_an_unresolved_peer_is_not_asked_for_once_per_frame(monkeypatch):
    """The shuttle has one thread; a lookup per frame while the peer's container comes up would
    stall every link on the machine."""
    calls = []
    monkeypatch.setattr(tp, "resolve_peer", lambda h: calls.append(h))
    now = [100.0]
    monkeypatch.setattr(tp.time, "monotonic", lambda: now[0])
    p = tp.Port(0, "r1", 9, bind_host="127.0.0.1")
    for _ in range(50):
        p.send(b"f")
    assert len(calls) == 1
    now[0] += tp.RETRY_S + 0.01
    p.send(b"f")
    assert len(calls) == 2
    p.sock.close()
