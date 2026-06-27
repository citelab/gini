#!/usr/bin/env python3
"""Minimal OpenFlow 1.0 switch — proves a controller completes the 1.0 handshake.

Mimics exactly what the gRouter's OpenFlow switch does on the wire (HELLO →
FEATURES_REPLY → echo/barrier keepalive), so we can confirm POX `gar` (Python 3)
accepts a 1.0 datapath and binds its app, independent of building the C gRouter.

  python3 of_hello_probe.py [host] [port]   # exits 0 once the controller binds us
"""
import socket
import struct
import sys
import time

OFP_VERSION = 0x01
HELLO, ERROR, ECHO_REQUEST, ECHO_REPLY = 0, 1, 2, 3
FEATURES_REQUEST, FEATURES_REPLY = 5, 6
SET_CONFIG, BARRIER_REQUEST, BARRIER_REPLY = 9, 18, 19
HDR = "!BBHI"


def header(msg_type, length, xid=0):
    return struct.pack(HDR, OFP_VERSION, msg_type, length, xid)


def features_reply(xid):
    # ofp_switch_features: datapath_id, n_buffers, n_tables, pad[3], capabilities, actions
    body = struct.pack("!QIB3xII", 0x0000_0000_0000_00AB, 256, 1, 0x000000C7, 0x00000FFF)
    return header(FEATURES_REPLY, 8 + len(body), xid) + body


def main():
    host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 6633
    deadline = time.time() + 20
    sock = None
    while time.time() < deadline and sock is None:
        try:
            sock = socket.create_connection((host, port), timeout=3)
        except OSError:
            time.sleep(0.5)
    if sock is None:
        print("PROBE: could not connect to controller", file=sys.stderr)
        return 2

    sock.sendall(header(HELLO, 8, 1))            # say hello first
    sock.settimeout(5)
    got_features = False
    buf = b""
    end = time.time() + 8
    while time.time() < end:
        try:
            chunk = sock.recv(4096)
        except socket.timeout:
            break
        if not chunk:
            break
        buf += chunk
        while len(buf) >= 8:
            ver, mtype, length, xid = struct.unpack(HDR, buf[:8])
            if len(buf) < length:
                break
            body, buf = buf[8:length], buf[length:]
            if mtype == HELLO:
                print("PROBE: controller HELLO (v%d)" % ver, file=sys.stderr)
            elif mtype == FEATURES_REQUEST:
                sock.sendall(features_reply(xid))
                got_features = True
                print("PROBE: sent FEATURES_REPLY", file=sys.stderr)
            elif mtype == ECHO_REQUEST:
                sock.sendall(header(ECHO_REPLY, 8 + len(body), xid) + body)
            elif mtype == BARRIER_REQUEST:
                sock.sendall(header(BARRIER_REPLY, 8, xid))
            # SET_CONFIG, flow_mods, etc. are fine to ignore for a handshake probe
        if got_features:
            end = min(end, time.time() + 1.5)    # linger briefly, then declare success
    sock.close()
    if got_features:
        print("PROBE: OK — controller completed the OpenFlow 1.0 handshake")
        return 0
    print("PROBE: FAILED — no FEATURES_REQUEST from controller", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
