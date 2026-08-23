# gini.samples.l4_lb -- a round-robin transport-layer load balancer.
#
# Clients send to a single virtual IP (VIP). For each new client, the controller
# picks the next backend server round-robin and installs two flows:
#
#   forward:  client -> VIP    rewrite dst IP to the chosen backend, then NORMAL
#   reverse:  backend -> client rewrite src IP back to the VIP,     then NORMAL
#
# The reverse rewrite is what makes the trick invisible to the client: replies
# appear to come from the VIP, not from whichever backend actually answered. The
# split is per source IP, so a given client always lands on the same backend for
# the life of the flow (idle_timeout seconds).
#
# This is a teaching load balancer: it balances per client IP (not per
# connection) and does no health checking. It is a good base to extend.
#
# Launch:
#   ./pox.py openflow.of_01 --port=6633 gini.samples.l4_lb \
#       --vip=10.0.1.100 --backends=10.0.1.11,10.0.1.12

from pox.core import core
import pox.openflow.libopenflow_01 as of
from pox.lib.addresses import IPAddr

log = core.getLogger()

_ETH_IP = 0x0800


class LoadBalancer(object):
    def __init__(self, connection, vip, backends):
        self.connection = connection
        self.vip = vip
        self.backends = backends
        self.next = 0
        self.assigned = {}              # client IP -> backend IP
        connection.addListeners(self)

    def _pick(self, client):
        if client not in self.assigned:
            self.assigned[client] = self.backends[self.next % len(self.backends)]
            self.next += 1
        return self.assigned[client]

    def _handle_PacketIn(self, event):
        packet = event.parsed
        ip = packet.find('ipv4')
        if ip is None:
            self._normal(event)
            return

        # Client -> VIP : choose a backend and rewrite the destination.
        if ip.dstip == self.vip:
            client = ip.srcip
            backend = self._pick(client)
            log.info("l4_lb: %s -> VIP -> %s", client, backend)

            fwd = of.ofp_flow_mod()
            fwd.match = of.ofp_match(dl_type=_ETH_IP, nw_src=client, nw_dst=self.vip)
            fwd.idle_timeout = 30
            fwd.actions.append(of.ofp_action_nw_addr.set_dst(backend))
            fwd.actions.append(of.ofp_action_output(port=of.OFPP_NORMAL))
            fwd.data = event.ofp
            self.connection.send(fwd)

            rev = of.ofp_flow_mod()
            rev.match = of.ofp_match(dl_type=_ETH_IP, nw_src=backend, nw_dst=client)
            rev.idle_timeout = 30
            rev.actions.append(of.ofp_action_nw_addr.set_src(self.vip))
            rev.actions.append(of.ofp_action_output(port=of.OFPP_NORMAL))
            self.connection.send(rev)
            return

        # Anything else: forward normally.
        self._normal(event)

    def _normal(self, event):
        msg = of.ofp_packet_out(data=event.ofp)
        msg.actions.append(of.ofp_action_output(port=of.OFPP_NORMAL))
        self.connection.send(msg)


class l4_lb(object):
    def __init__(self, vip, backends):
        self.vip = vip
        self.backends = backends
        core.openflow.addListeners(self)

    def _handle_ConnectionUp(self, event):
        event.connection.send(of.ofp_flow_mod(command=of.OFPFC_DELETE))
        LoadBalancer(event.connection, self.vip, self.backends)
        log.info("gini.samples.l4_lb ready on %s (VIP %s -> %s)",
                 event.dpid, self.vip,
                 ", ".join(str(b) for b in self.backends))


def launch(vip="10.0.1.100", backends="10.0.1.11,10.0.1.12"):
    pool = [IPAddr(b) for b in backends.split(",")]
    core.registerNew(l4_lb, IPAddr(vip), pool)
