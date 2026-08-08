"""Machine shuttle: the real entrypoint for a 'machine' container.

Creates one or more TAP interfaces inside the container (Linux; needs NET_ADMIN +
/dev/net/tun), assigns each its experiment IP, and bridges each TAP to its link's UDP
endpoint: TAP frame -> UDP datagram and back. A machine with several interfaces is
multi-homed (one TAP/IP per subnet); the default route uses the configured gateway.

The container's own docker eth0 stays as the management/transport NIC; experiment
traffic rides the TAPs (gini0, gini1, … so they don't clash with docker's eth0).

NODE_CONFIG = {"name":"m1", "gw":"10.0.1.1", "ifaces":[
    {"ip":"10.0.1.10/24","mac":"..","tap":"gini0","port":{...}},
    {"ip":"10.0.2.10/24","mac":"..","tap":"gini1","port":{...}} ]}
"""
from __future__ import annotations

import fcntl
from array import array
import json
import os
import selectors
import struct
import subprocess
import sys

from .transport import Port

TUNSETIFF = 0x400454CA
TUNSETOFFLOAD = 0x400454D0        # _IOW('T', 208, unsigned int)
IFF_TAP = 0x0002
IFF_NO_PI = 0x1000

# External uplink for the Internet element (NAT gateway). Must match the `wan` network
# in services/orchestrator.py so the gateway can find its WAN interface + default route.
WAN_SUBNET_PREFIX = "192.168.244."
WAN_GATEWAY = "192.168.244.1"
EXP_SUPERNET = "10.0.0.0/8"


_BIG_ENDIAN = sys.byteorder == "big"


def _ones_complement(data) -> int:
    """The 16-bit one's-complement sum used by every IP checksum.

    Written with array/sum rather than a Python loop over bytes: this runs on every
    forwarded frame, and the naive version costs ~53 us per 1400-byte packet, which would
    cap the whole data plane at a couple of hundred Mbps and quietly distort the iperf
    experiments students are supposed to be able to trust.
    """
    raw = bytes(data)
    if len(raw) & 1:
        raw += b"\x00"                       # pad an odd tail, per RFC 1071
    words = array("H", raw)
    if not _BIG_ENDIAN:
        words.byteswap()                     # network order
    total = sum(words)
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return total


def repair_l4_checksum(frame: bytearray) -> bool:
    """Recompute a TCP/UDP checksum that the sender left unfinished. True if changed.

    WHY THE DATA PLANE HAS TO DO THIS. A checksum is normally completed by whichever
    kernel transmits the packet — either in software, or by the NIC when the driver
    advertises offload. Neither happens here, and it took a long time to see why:

      * Docker Desktop for macOS answers TCP from a userspace proxy inside the same
        Linux VM. Its replies reach a container's `eth0` with the checksum field still
        holding a partial value AND a flag saying it has already been verified.
      * That is legitimate for a packet addressed to the container: the stack trusts the
        flag and never looks at the bytes. But the NAT gateway FORWARDS these packets,
        and routers do not validate layer-4 checksums, so the bad bytes go straight out.
      * At transmit time the kernel sees a packet it believes is already correct, so it
        will not recompute — turning off tap offload changes nothing, which is exactly
        what we measured: `tx-checksumming: off` on both ends, packet still wrong.

    The real machine behind the gateway then validates properly, fails, and drops in
    silence. Every TCP connection to the internet dies mid-handshake while ping works at
    any size and DNS works, because ICMP is always checksummed in software and DNS went
    to Docker's loopback resolver without ever crossing a tap.

    So the last component that can see the actual bytes has to fix them, and that is us.
    Cheap in practice: only IPv4 TCP/UDP is touched, and only when the checksum really is
    wrong, which after the handshake is essentially never on a healthy path.
    """
    n = len(frame)
    if n < 34 or frame[12] != 0x08 or frame[13] != 0x00:      # not IPv4
        return False
    ip = 14
    if (frame[ip] >> 4) != 4:
        return False
    ihl = (frame[ip] & 0x0F) * 4
    if ihl < 20 or ip + ihl > n:
        return False
    proto = frame[ip + 9]
    if proto not in (6, 17):                                  # TCP, UDP
        return False
    # Fragments carry only part of the payload; the checksum cannot be computed here.
    if ((frame[ip + 6] << 8 | frame[ip + 7]) & 0x3FFF) != 0:
        return False

    total_len = (frame[ip + 2] << 8) | frame[ip + 3]
    if total_len < ihl or ip + total_len > n:
        return False                                          # truncated or padded oddly
    l4 = ip + ihl
    l4_len = total_len - ihl
    if l4_len < (20 if proto == 6 else 8):
        return False

    ck_off = l4 + (16 if proto == 6 else 6)
    old = (frame[ck_off] << 8) | frame[ck_off + 1]
    # A UDP checksum of zero means "not computed", which is legal. Leave it alone rather
    # than inventing one — some protocols rely on it being absent.
    if proto == 17 and old == 0:
        return False

    frame[ck_off] = 0
    frame[ck_off + 1] = 0
    view = memoryview(frame)
    # pseudo-header: src, dst, zero, proto, length
    total = _ones_complement(view[ip + 12:ip + 20]) + proto + l4_len
    total += _ones_complement(view[l4:l4 + l4_len])
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    new = (~total) & 0xFFFF
    if new == 0 and proto == 17:
        new = 0xFFFF                                          # 0 means "none" for UDP
    if new == old:
        frame[ck_off] = old >> 8
        frame[ck_off + 1] = old & 0xFF
        return False
    frame[ck_off] = new >> 8
    frame[ck_off + 1] = new & 0xFF
    return True


def open_tap(name: str) -> int:
    fd = os.open("/dev/net/tun", os.O_RDWR)
    ifr = struct.pack("16sH", name.encode(), IFF_TAP | IFF_NO_PI)
    fcntl.ioctl(fd, TUNSETIFF, ifr)

    # Turn OFF every hardware offload the tap pretends to have. This is load-bearing.
    #
    # A tap device advertises NETIF_F_HW_CSUM by default, which tells the kernel "do not
    # bother computing TCP/UDP checksums, the hardware will finish them". There is no
    # hardware: the next thing to touch the frame is the read() below, and whatever it
    # reads goes onto the wire verbatim. So any packet whose checksum was still pending
    # left this container mathematically corrupt, and the far end dropped it in silence.
    #
    # It only bites on FORWARDED traffic, which is why it hid for so long. A locally
    # generated packet gets its checksum completed on the way out; a packet merely
    # passing through keeps whatever state it arrived with. On Docker Desktop for macOS
    # the replies from the internet are generated by a userspace TCP proxy inside the
    # same Linux VM, so they reach the NAT gateway with a PARTIAL checksum — and the
    # gateway then forwarded them, unfinished, to every machine behind it.
    #
    # Symptoms, all explained by this one line: ping works at any size, DNS works, the
    # SYN leaves correctly and the SYN-ACK comes back correctly at the gateway — but the
    # machine never sees it, and every TCP connection to the internet times out during
    # the handshake. ICMP is immune because the kernel always checksums it in software;
    # DNS survived because it went to Docker's loopback resolver and never crossed a tap.
    # The give-away in a capture is the SAME checksum on every inbound packet (the
    # pseudo-header partial sum) while the correct values all differ.
    #
    # Passing 0 also disables TSO/UFO, which is equally necessary: segmentation offload
    # would hand us frames far larger than the MTU, which the UDP transport cannot carry.
    try:
        fcntl.ioctl(fd, TUNSETOFFLOAD, 0)
    except OSError as exc:
        # Not fatal, but it must never be silent — this failing looks exactly like a
        # broken network, and the last time it was quiet it cost a full debugging session.
        print(f"[shuttle] WARNING: could not disable offloads on {name} ({exc}). "
              f"TCP through this container may be dropped for bad checksums.",
              file=sys.stderr)
    return fd


def configure_iface(name: str, mac: str, cidr: str) -> None:
    subprocess.run(["ip", "link", "set", name, "address", mac], check=True)
    subprocess.run(["ip", "addr", "add", cidr, "dev", name], check=True)
    subprocess.run(["ip", "link", "set", name, "up"], check=True)
    subprocess.run(["ip", "link", "set", name, "mtu", "1400"], check=True)   # absorb UDP encap


def add_default_route(gw: str, exp_net: str = EXP_SUPERNET) -> None:
    # Route the experiment supernet via the gateway. Connected /24s (from `ip addr
    # add`) are more specific and win for on-link subnets; this catches everything
    # else. The container's default route stays on docker eth0 for the UDP transport.
    subprocess.run(["ip", "route", "replace", exp_net, "via", gw], check=False)


def set_fabric_default(gw: str) -> None:
    """Send the *real* default route (0.0.0.0/0) into the fabric via `gw`, so internet-
    bound traffic egresses through the drawn routers + Internet element (not docker eth0).
    Used on ordinary hosts when an Internet element is present on the canvas."""
    subprocess.run(["ip", "route", "replace", "default", "via", gw], check=False)


def cut_default_route() -> None:
    """Faithful mode with no Internet element: remove the docker-eth0 default route so this
    host has NO path to the internet (the management NIC isn't a back door). Inter-container
    traffic and the simulated fabric still work — those use connected/`10.0.0.0/8` routes."""
    subprocess.run(["ip", "route", "del", "default"], check=False)
    print("[gini] faithful mode: default route removed (no internet on this host)",
          file=sys.stderr)


def _wan_iface() -> str | None:
    """The interface attached to the external `wan` bridge (its IP is in WAN_SUBNET)."""
    out = subprocess.run(["ip", "-o", "-4", "addr", "show"],
                         capture_output=True, text=True).stdout
    for line in out.splitlines():
        parts = line.split()              # "<idx>: <ifname>    inet <ip>/<pfx> ..."
        if len(parts) >= 4 and parts[1] != "lo" and parts[3].startswith(WAN_SUBNET_PREFIX):
            return parts[1]
    return None


def _enable_ip_forward() -> None:
    try:
        with open("/proc/sys/net/ipv4/ip_forward", "w") as f:    # namespaced; NET_ADMIN ok
            f.write("1")
    except OSError:
        subprocess.run(["sysctl", "-w", "net.ipv4.ip_forward=1"], check=False)


def vnf_commands(nf: str, rules: str, gw: str | None = None) -> list[list[str]]:
    """The commands that make this container an inline VNF of kind `nf` (a network function
    in the forwarding path). PURE — returns the argv lists; `setup_vnf` runs them — so the
    rule-to-iptables translation is unit-testable without Docker.

    firewall/block are real (iptables on the FORWARD chain); ids/cache/shaper forward-only
    for now (no real data-plane backend yet — labeled illustrative in the UI)."""
    cmds: list[list[str]] = []
    if gw:                              # onward route so transit traffic continues to egress
        cmds.append(["ip", "route", "replace", EXP_SUPERNET, "via", gw])
    lines = [ln.strip() for ln in (rules or "").replace(",", "\n").splitlines() if ln.strip()]
    if nf == "firewall":
        for ln in lines:
            toks = ln.split()
            verb = toks[0].lower() if toks else ""
            rest = toks[1:]
            if verb in ("deny", "drop", "block") and rest:
                if len(rest) >= 2 and rest[0].lower() == "from":
                    cmds.append(["iptables", "-A", "FORWARD", "-s", rest[1], "-j", "DROP"])
                else:
                    cmds.append(["iptables", "-A", "FORWARD", "-d", rest[0], "-j", "DROP"])
    elif nf == "block":
        for ln in lines:
            cmds.append(["iptables", "-A", "FORWARD", "-d", ln.split()[0], "-j", "DROP"])
    return cmds


def setup_vnf(cfg: dict) -> None:
    """Inline VNF: forward between the interfaces and apply the network function."""
    _enable_ip_forward()
    for cmd in vnf_commands(cfg.get("nf", ""), cfg.get("nf_rules", ""), cfg.get("gw")):
        subprocess.run(cmd, check=False)
    print(f"[{cfg.get('name')}] VNF '{cfg.get('nf')}' inline (rules: {cfg.get('nf_rules')!r})",
          file=sys.stderr)


def setup_nat_gateway(cfg: dict) -> None:
    """Turn this container into the lab's NAT gateway (the drawn Internet element):
    enable IP forwarding, pin the default route out the external uplink, MASQUERADE the
    fabric out to the world, and route the experiment supernet back via the local router
    so replies reach the hosts behind us."""
    _enable_ip_forward()
    subprocess.run(["ip", "route", "replace", "default", "via", WAN_GATEWAY], check=False)
    wan = _wan_iface()
    if wan:
        subprocess.run(["iptables", "-t", "nat", "-A", "POSTROUTING",
                        "-o", wan, "-j", "MASQUERADE"], check=False)
    fgw = cfg.get("fabric_gw")
    if fgw:                              # return path: GINI subnets reachable via the router
        subprocess.run(["ip", "route", "replace", EXP_SUPERNET, "via", fgw], check=False)
    print(f"[{cfg.get('name')}] NAT gateway: {EXP_SUPERNET} via {fgw} "
          f"<-> internet via {wan}", file=sys.stderr)


def main() -> None:
    cfg = json.loads(os.environ["NODE_CONFIG"])
    ifaces = cfg.get("ifaces")
    if ifaces is None:                       # back-compat with the old single-iface shape
        ifaces = [{"ip": cfg["ip"], "mac": cfg["mac"],
                   "tap": cfg.get("tap", "gini0"), "port": cfg["port"]}]

    sel = selectors.DefaultSelector()
    fd_port: dict[int, Port] = {}            # tap fd -> its uplink Port
    port_fd: dict[int, int] = {}             # port socket fileno -> tap fd
    for idx, itf in enumerate(ifaces):
        tap = itf.get("tap", f"gini{idx}")
        fd = open_tap(tap)
        configure_iface(tap, itf["mac"], itf["ip"])
        port = Port.from_cfg(itf["port"], name=tap)
        fd_port[fd] = port
        port_fd[port.sock.fileno()] = fd
        sel.register(fd, selectors.EVENT_READ, "tap")
        sel.register(port.sock, selectors.EVENT_READ, "udp")
        print(f"[{cfg['name']}] {tap} {itf['ip']} <-> UDP "
              f"{port.peer_host}:{port.peer_port}", file=sys.stderr)

    if cfg.get("gateway"):                       # the drawn Internet element = NAT gateway
        setup_nat_gateway(cfg)
    elif cfg.get("nf"):                          # an inline VNF (network function in the path)
        setup_vnf(cfg)
    elif cfg.get("fabric_default") and cfg.get("gw"):
        set_fabric_default(cfg["gw"])            # internet egresses through the fabric
    else:
        if cfg.get("gw"):
            add_default_route(cfg["gw"])         # experiment supernet via the router
        if cfg.get("cut_default"):               # faithful mode: no internet on this host
            cut_default_route()

    # Counted, and announced the first time it happens: a data plane quietly rewriting
    # packets is exactly the kind of thing that should be visible in a teaching tool.
    repaired = [0]

    while True:
        for key, _ in sel.select():
            if key.data == "tap":
                frame = os.read(key.fileobj, 65535)          # TAP -> UDP
                if frame:
                    # Last chance to make the bytes true. Anything FORWARDED through this
                    # container may carry a checksum its originator never finished — see
                    # repair_l4_checksum() — and once it is on the fabric the receiving
                    # machine will drop it without a word. Locally generated traffic is
                    # already correct, so this is a no-op on the common path.
                    buf = bytearray(frame)
                    if repair_l4_checksum(buf):
                        repaired[0] += 1
                        if repaired[0] in (1, 100, 10000):
                            print(f"[{cfg['name']}] repaired {repaired[0]} unfinished "
                                  f"L4 checksum(s) on forwarded traffic", file=sys.stderr)
                        frame = bytes(buf)
                    fd_port[key.fileobj].send(frame)
            else:
                port = fd_port[port_fd[key.fileobj.fileno()]]
                frame = port.recv()                          # UDP -> TAP
                if frame:
                    os.write(port_fd[key.fileobj.fileno()], frame)


if __name__ == "__main__":
    main()
