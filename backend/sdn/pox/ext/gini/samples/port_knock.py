# gini.samples.port_knock -- a port-knocking firewall.
#
# A protected service (default: telnet, TCP 23) on a server is closed to everyone.
# A client opens it by "knocking": sending TCP packets to a secret sequence of
# ports, in order (default 1111, 2222, 3333). Once a client completes the
# sequence, the controller installs a flow that lets that client -- and only that
# client -- reach the protected port for a while. A wrong knock resets that
# client's progress to zero.
#
# This is a small but real example of putting policy in the controller: the
# firewall rule does not exist until the right sequence is observed, and it is
# scoped to the source IP that earned it.
#
# Ordinary traffic is delivered by gini.samples._l2 and NOT installed as a flow: a learned
# "dst = the server's MAC" rule would carry knocks -- and the protected port -- straight past
# the controller, and the door would neither need knocking nor ever open. It used to be sent
# out OFPP_NORMAL, which on the GINI Flow Switch is the router's IP pipeline and delivered
# nothing at all on a flat segment.
#
# The server address must agree with what GINI assigned. With automatic addressing,
# hosts on a segment are numbered .10, .11, ... in the order they were WIRED; the
# default assumes client first, then the server -- Client .10, Server .11 -- the same
# convention as l4_lb and redirect. Get it wrong and the failure is the quiet kind: the
# real server's port is never guarded, so the door is simply open to everyone. The log
# says so the first time it sees the protected port reached at another address.
#
# Launch:
#   ./pox.py openflow.of_01 --port=6633 gini.samples.port_knock \
#       --server=10.0.1.11 --port=23 --sequence=1111,2222,3333

from pox.core import core
import pox.openflow.libopenflow_01 as of
from pox.lib.addresses import IPAddr

from gini.samples._l2 import L2

log = core.getLogger()

_ETH_IP = 0x0800
_IP_TCP = 6


class PortKnock(object):
    def __init__(self, connection, server, protected, sequence):
        self.connection = connection
        self.server = server            # IPAddr of the protected server
        self.protected = protected      # protected TCP port
        self.sequence = sequence        # list of knock ports, in order
        self.progress = {}              # src IP -> number of correct knocks so far
        self.warned_elsewhere = False   # said once that --server looks wrong
        self.l2 = L2(connection)        # delivers ordinary traffic, installs nothing
        connection.addListeners(self)

    def _handle_PacketIn(self, event):
        packet = event.parsed
        self.l2.learn(event)
        ip = packet.find('ipv4')
        tcp = packet.find('tcp')
        if ip is None or tcp is None:
            self.l2.forward(event)      # non-TCP/IP: just forward
            return

        src = ip.srcip
        dport = tcp.dstport

        # The protected port reached at some OTHER address: almost always --server naming
        # the wrong host, which leaves the real one unguarded. Say it once; still deliver.
        if dport == self.protected and ip.dstip != self.server and not self.warned_elsewhere:
            self.warned_elsewhere = True
            log.warning("port_knock: %s:%d is being reached, but the guarded server is %s -- "
                        "check --server against `ip addr` on the server", ip.dstip,
                        self.protected, self.server)

        # A packet aimed at the protected service.
        if ip.dstip == self.server and dport == self.protected:
            if self.progress.get(src, 0) >= len(self.sequence):
                self._open(event, src)  # client knocked in -> allow
            else:
                log.info("port_knock: blocked %s -> %s:%d (not knocked in)",
                         src, self.server, self.protected)
            return                      # never flood traffic to the protected port

        # Is this the next knock this client owes us?
        step = self.progress.get(src, 0)
        if step < len(self.sequence) and dport == self.sequence[step]:
            self.progress[src] = step + 1
            log.info("port_knock: %s knock %d/%d ok",
                     src, step + 1, len(self.sequence))
            return                      # consume the knock; do not forward it
        elif dport in self.sequence:
            self.progress[src] = 0      # a knock, but out of order -> reset
            log.info("port_knock: %s knocked out of order -> reset", src)
            return

        # Ordinary traffic: deliver it, and keep watching.
        self.l2.forward(event)

    def _open(self, event, src):
        # Open the door to the server's own port. The client addressed the server, so the
        # frame's destination MAC is the server's; if the switch has not seen that MAC yet,
        # let this packet through by flooding and let the next one install the rule.
        out = self.l2.mac_to_port.get(event.parsed.dst)
        if out is None:
            self.l2.send(event.ofp, of.OFPP_FLOOD)
            return
        msg = of.ofp_flow_mod()
        msg.match = of.ofp_match(dl_type=_ETH_IP, nw_proto=_IP_TCP,
                                 nw_src=src, nw_dst=self.server,
                                 tp_dst=self.protected)
        msg.idle_timeout = 60
        msg.actions.append(of.ofp_action_output(port=out))
        msg.data = event.ofp
        self.connection.send(msg)
        log.info("port_knock: %s knocked in -> %s:%d open for 60s",
                 src, self.server, self.protected)


class port_knock(object):
    def __init__(self, server, protected, sequence):
        self.server = server
        self.protected = protected
        self.sequence = sequence
        core.openflow.addListeners(self)

    def _handle_ConnectionUp(self, event):
        event.connection.send(of.ofp_flow_mod(command=of.OFPFC_DELETE))
        PortKnock(event.connection, self.server, self.protected, self.sequence)
        log.info("gini.samples.port_knock ready on %s "
                 "(protect %s:%d, knock %s)",
                 event.dpid, self.server, self.protected,
                 "-".join(str(p) for p in self.sequence))


def launch(server="10.0.1.11", port="23", sequence="1111,2222,3333"):
    seq = [int(p) for p in sequence.split(",")]
    core.registerNew(port_knock, IPAddr(server), int(port), seq)
