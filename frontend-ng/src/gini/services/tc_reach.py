"""Can this computer reach the course server right now — and if not, why not.

The question behind the GINI server pill in the toolbar. Students hand in from home, the course
server is reachable only on the campus VPN, and the failure they were shown was the operating
system's: `<urlopen error [Errno 8] nodename nor servname provided, or not known>`. A student
cannot act on that, and a teacher reading it in a complaint cannot tell a student who never
connected the VPN from a server that is down. So the pill answers online or offline, and offline
carries the one fact that decides who has to fix it:

  * **not found** — the server's NAME does not resolve. The course server's name is only known on
    the campus network or its VPN, so this is the student's to fix: connect the VPN.
  * **no answer** — the name resolved and nothing answered in time. A half-connected VPN looks
    exactly like this, and so does a server that is down.
  * **refused** — something answered and turned the connection away: the server's machine is up
    and the course server on it is not. That one is the teacher's.

**Is a VPN on? Not answerable by looking for one.** Every Mac has several `utun` tunnel
interfaces at all times (iCloud and other system services make them), so "is there a tunnel?"
says VPN for every Mac student. The question that works is how the operating system would ROUTE
a packet to the server: through a tunnel or through Wi-Fi/Ethernet. That is asked of the route
table (`route get`, `ip route get`, `Find-NetRoute`) — nothing is sent — and it handles a
split-tunnel VPN, where only the campus addresses go through the tunnel, the way it should.

**Which VPN** is best effort: the running VPN client, recognised by process name, or a Windows
adapter description. One that is not recognised shows as "a VPN". It is diagnosis for a report,
never a decision — online/offline is decided by actually connecting.

Nothing here leaves the machine except the DNS lookup and one TCP connection to the course
server's own port. The result is shown to the student and copied only when they choose to.

Deliberately Qt-free and injectable, like `outbox`: the decisions are tested without a network.
"""
from __future__ import annotations

import ipaddress
import re
import socket
import subprocess
import sys
import time
import urllib.parse

#: Seconds to wait for the TCP connection. Short: the pill re-checks every minute or two, and a
#: server that has not answered in five seconds is not one a hand-in should be started against.
CONNECT_TIMEOUT = 5.0

ONLINE, NOT_FOUND, NO_ANSWER, REFUSED, NO_ROUTE = "", "not_found", "no_answer", "refused", "no_route"

#: What the pill says after "GINI server · " for each reason.
SHORT = {NOT_FOUND: "not found", NO_ANSWER: "no answer", REFUSED: "refused", NO_ROUTE: "no network"}

# A tunnel interface, by name, on macOS and Linux. Names only mean something once the ROUTE goes
# through one — see the module docstring for why their mere presence means nothing.
_TUNNEL_NAME = re.compile(r"^(utun|ipsec|ppp|tun|tap|wg|cscotun|gpd|tailscale|nordlynx|proton|"
                          r"zt|vpn)", re.IGNORECASE)

# Known VPN clients: (name to show, process-name fragments, adapter-description fragments).
# Lower-case, matched as substrings of a process's base name or a Windows adapter description.
_CLIENTS: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("Cisco Secure Client", ("vpnagentd", "vpnagent", "vpnui", "cisco secure client",
                             "anyconnect", "csc_ui"), ("anyconnect", "cisco")),
    ("GlobalProtect", ("pangps", "pangpa", "globalprotect"), ("pangp", "globalprotect")),
    ("FortiClient", ("forticlient", "fortitray", "fortisslvpn"), ("fortinet", "forticlient")),
    ("Ivanti Secure Access", ("dsaccessservice", "pulse secure", "pulsesecure", "ivanti"),
     ("juniper", "pulse", "ivanti")),
    ("Cloudflare WARP", ("warp-svc", "cloudflare warp"), ("cloudflare",)),
    ("Tailscale", ("tailscaled", "tailscale-ipn"), ("tailscale",)),
    ("NordVPN", ("nordvpn",), ("nordlynx", "nordvpn")),
    ("ProtonVPN", ("protonvpn", "proton vpn"), ("proton",)),
    ("Mullvad", ("mullvad",), ("mullvad",)),
    ("ExpressVPN", ("expressvpn",), ("expressvpn",)),
    ("Surfshark", ("surfshark",), ("surfshark",)),
    ("WireGuard", ("wireguard",), ("wireguard",)),
    ("OpenVPN", ("openvpn", "tunnelblick"), ("tap-windows", "openvpn")),
)


def target(url: str) -> tuple[str, int]:
    """(host, port) of the course server, or ("", 0) when none is configured."""
    try:
        u = urllib.parse.urlsplit((url or "").strip())
        return (u.hostname or "", u.port or (443 if u.scheme != "http" else 80))
    except ValueError:
        return "", 0


# --------------------------------------------------------------------------- #
# the operating system, asked — each one injectable, each one never raising
# --------------------------------------------------------------------------- #

def _run(args: list[str], timeout: float = 6.0) -> str:
    """A command's output, or "" — a diagnosis must never take the pill down with it."""
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)       # no console flash on Windows
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                           creationflags=flags, check=False)
        return r.stdout or ""
    except Exception:                                            # noqa: BLE001
        return ""


def route_to(ip: str) -> dict:
    """Which interface the operating system would send a packet to `ip` through:
    `{interface, description}`, or {} when it cannot say. Reads the route table; sends nothing."""
    if not ip:
        return {}
    if sys.platform == "darwin":
        m = re.search(r"interface:\s*(\S+)", _run(["route", "-n", "get", ip]))
        return {"interface": m.group(1), "description": ""} if m else {}
    if sys.platform.startswith("win"):
        ps = (f"$r = Find-NetRoute -RemoteIPAddress '{ip}' | Select-Object -First 1; "
              f"$a = Get-NetAdapter -InterfaceIndex $r.InterfaceIndex; "
              f"\"$($a.Name)|$($a.InterfaceDescription)\"")
        out = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], timeout=10)
        name, _, desc = out.strip().partition("|")
        return {"interface": name.strip(), "description": desc.strip()} if name.strip() else {}
    m = re.search(r"\bdev\s+(\S+)", _run(["ip", "route", "get", ip]))
    return {"interface": m.group(1), "description": ""} if m else {}


def processes() -> list[str]:
    """Base names of the running processes, lower-cased. [] when they cannot be listed."""
    if sys.platform.startswith("win"):
        out = _run(["tasklist", "/fo", "csv", "/nh"])
        names = [line.split('","')[0].strip('"') for line in out.splitlines() if line]
    else:
        names = [line.strip().rsplit("/", 1)[-1] for line in _run(["ps", "-axo", "comm="])
                 .splitlines()]
    return [n.lower() for n in names if n]


# --------------------------------------------------------------------------- #
# the decisions — pure
# --------------------------------------------------------------------------- #

def is_tunnel(route: dict) -> bool:
    """Does this route go through a VPN tunnel?"""
    name = str(route.get("interface") or "")
    desc = str(route.get("description") or "").lower()
    if desc:                                                     # Windows: the adapter says so
        return any(f in desc for _n, _p, frags in _CLIENTS for f in frags) or any(
            w in desc for w in ("vpn", "tunnel", "virtual private", "tap-", "wintun"))
    return bool(_TUNNEL_NAME.match(name))


def vpn_clients(procs: list[str], route: dict | None = None) -> list[str]:
    """The VPN clients that are running, by name, in the order of `_CLIENTS`."""
    desc = str((route or {}).get("description") or "").lower()
    out = []
    for name, procfrags, descfrags in _CLIENTS:
        if any(f in p for p in procs for f in procfrags) or (
                desc and any(f in desc for f in descfrags)):
            out.append(name)
    return out


def probe(url: str, *, resolve=socket.getaddrinfo, connect=socket.create_connection,
          route=route_to, procs=processes, now=time.time) -> dict:
    """Try the course server once and say what happened.

    Returns `{configured, online, reason, host, port, address, route, tunnel, vpn, checked,
    error}`. `reason` is one of `NOT_FOUND`, `NO_ANSWER`, `REFUSED`, `NO_ROUTE`, or "" when it is
    online. `route` and `tunnel` describe the way to the server when its address is known, and the
    way to the internet when it is not — which is the question then ("is the VPN on at all?").
    """
    host, port = target(url)
    out = {"configured": bool(host), "online": False, "reason": "", "host": host, "port": port,
           "address": "", "route": {}, "tunnel": False, "vpn": [], "checked": now(), "error": ""}
    if not host:
        return out
    try:
        infos = resolve(host, port, type=socket.SOCK_STREAM)
        out["address"] = str(infos[0][4][0]) if infos else ""
    except (socket.gaierror, UnicodeError, OSError) as e:
        out["reason"], out["error"] = NOT_FOUND, str(e)
    if out["address"]:
        try:
            with connect((out["address"], port), timeout=CONNECT_TIMEOUT):
                out["online"] = True
        except ConnectionRefusedError as e:
            out["reason"], out["error"] = REFUSED, str(e)
        except TimeoutError as e:                    # socket.timeout too
            out["reason"], out["error"] = NO_ANSWER, str(e) or "timed out"
        except OSError as e:
            # "Network is unreachable" / "No route to host": no way out at all, or no way to
            # that address. Either way nothing answered.
            out["reason"] = NO_ROUTE if e.errno in (51, 65, 101, 113, 10051, 10065) else NO_ANSWER
            out["error"] = str(e)
    # The way there. To the server when we know where it is; otherwise to the internet at large,
    # because "not found" with every packet going out over Wi-Fi means the VPN is simply off.
    try:
        ipaddress.ip_address(out["address"])
        way = out["address"]
    except ValueError:
        way = "1.1.1.1"
    try:
        out["route"] = route(way) or {}
    except Exception:                                            # noqa: BLE001
        out["route"] = {}
    out["tunnel"] = is_tunnel(out["route"])
    try:
        out["vpn"] = vpn_clients(procs(), out["route"])
    except Exception:                                            # noqa: BLE001
        out["vpn"] = []
    return out


def pill_text(r: dict) -> str:
    """What the toolbar pill says."""
    if r.get("online"):
        return "GINI server"
    return f"GINI server · {SHORT.get(r.get('reason', ''), 'offline')}"


def network_line(r: dict) -> str:
    """How this computer is connected, in one line — "VPN (Cisco Secure Client)" or "direct"."""
    vpn = ", ".join(r.get("vpn") or [])
    if r.get("tunnel"):
        return f"VPN ({vpn})" if vpn else "VPN"
    if vpn:
        # A client is running but the route does not use it: disconnected, or connected to a
        # VPN that does not carry this server's traffic. Both are worth a student knowing.
        return f"direct — {vpn} is running but not carrying this traffic"
    return "direct (no VPN)"


def advice(r: dict) -> str:
    """One sentence a student can act on, or "" when there is nothing to do."""
    if r.get("online") or not r.get("configured"):
        return ""
    host = r.get("host", "the course server")
    reason = r.get("reason")
    if reason == NOT_FOUND:
        apps = ", ".join(r.get("vpn") or [])
        if r.get("tunnel"):
            return (f"This computer cannot find {host}. A VPN is connected"
                    f"{f' ({apps})' if apps else ''}, but it does not seem to be the campus VPN "
                    f"— connect to the campus VPN instead.")
        if apps:
            # The common case on a student's laptop: the campus client is installed and running,
            # and simply not connected. Saying "that is not the campus VPN" would be wrong.
            return (f"This computer cannot find {host}, which is only known on the campus "
                    f"network or its VPN. {apps} is running but not connected — connect it to "
                    f"the campus VPN.")
        return (f"This computer cannot find {host}, which is only known on the campus network "
                f"or its VPN. Connect to the campus VPN.")
    if reason == NO_ANSWER:
        return (f"{host} did not answer. If you are off campus, check that the campus VPN is "
                f"connected — a half-connected VPN looks exactly like this. If it is, the server "
                f"may be down: tell your instructor.")
    if reason == REFUSED:
        return (f"{host} turned the connection away — its machine is up but the course server "
                f"is not answering. Tell your instructor.")
    if reason == NO_ROUTE:
        return "This computer has no network connection that reaches the course server."
    return f"{host} could not be reached."


def details(r: dict, gini_version: str = "") -> str:
    """The whole picture as plain text — what Copy puts on the clipboard for a report."""
    if not r.get("configured"):
        return "No course server is configured (Settings → Teaching Center)."
    when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(r.get("checked") or 0)))
    status = "online" if r.get("online") else f"OFFLINE — {SHORT.get(r.get('reason'), '?')}"
    route = r.get("route") or {}
    way = route.get("interface") or "unknown"
    if route.get("description"):
        way += f" ({route['description']})"
    lines = [f"Course server: {r.get('host')}:{r.get('port')} — {status}",
             f"Address found: {r.get('address') or 'none (the name did not resolve)'}",
             f"Network: {network_line(r)}",
             (f"Route {'to the server' if r.get('address') else 'to the internet'}: via {way}"
              f"{' — a VPN tunnel' if r.get('tunnel') else ''}"),
             f"VPN apps running: {', '.join(r.get('vpn') or []) or 'none recognised'}"]
    if r.get("error"):
        lines.append(f"Error: {r['error']}")
    lines.append(f"Checked: {when}")
    if gini_version:
        lines.append(f"gBuilder: {gini_version} · {sys.platform}")
    return "\n".join(lines)
