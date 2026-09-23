# gini.samples.l4_lb -- a round-robin transport-layer load balancer.
#
# Clients send to a single virtual IP (VIP). For each new client, the controller
# picks the next backend server round-robin and installs two flows:
#
#   forward:  client -> VIP     rewrite dst MAC+IP to the chosen backend, out its port
#   reverse:  backend -> client rewrite src MAC+IP back to the VIP,       out the client's port
#
# The reverse rewrite is what makes the trick invisible to the client: replies
# appear to come from the VIP, not from whichever backend actually answered. The
# split is per source IP, so a given client always lands on the same backend for
# the life of the flow (idle_timeout seconds).
#
# This is a teaching load balancer: it balances per client IP (not per
# connection) and does no health checking. It is a good base to extend.
#
# Three things this app has to do that a textbook sketch leaves out -- each one
# was a way the VIP silently did not answer:
#
#   * ANSWER ARP FOR THE VIP. Nothing owns 10.0.1.100, so a client on the same
#     segment asks "who has 10.0.1.100?" and, unanswered, never sends a single IP
#     packet -- the flow rules never even get a chance. The controller replies with
#     a made-up MAC (--vmac), which is what "advertises a VIP" means in practice.
#
#   * REWRITE THE MAC, NOT JUST THE IP. The client addressed its frame to the VIP's
#     MAC. Rewriting only the IP delivers a frame the backend's NIC is not addressed
#     by and drops. The forward flow sets dl_dst to the backend; the reverse flow
#     sets dl_src to the VIP's MAC so the client's ARP entry stays consistent.
#
#   * FORWARD TO REAL PORTS, NEVER OFPP_NORMAL. On the GINI Flow Switch (the gRouter
#     in --openflow mode) NORMAL means "hand to the ROUTER's own IP pipeline", not
#     "behave like an L2 switch". On a flat segment the router has no interfaces or
#     routes to use, so NORMAL was a black hole: with it, even a client fetching a
#     backend DIRECTLY failed, because ARP between hosts never crossed the switch.
#     Everything that is not VIP traffic is forwarded the way gini.samples.switch
#     does it -- learn MAC -> port, flood the unknown -- by gini.samples._l2, which
#     the other samples share for the same reason.
#
# The backend list is matched against addresses, so it must agree with what GINI
# actually assigned. With automatic addressing hosts are numbered .10, .11, ... in
# the order they were placed, which is NOT the textbook's .11/.12 for the web
# servers. Either turn on manual addressing and set them, or pass --backends with
# the addresses `ip addr` shows on each backend. A pool member that shows up
# sending to the VIP is logged as a warning, because it is almost always a client.
#
# Launch:
#   ./pox.py openflow.of_01 --port=6633 gini.samples.l4_lb \
#       --vip=10.0.1.100 --backends=10.0.1.11,10.0.1.12

from pox.core import core
import pox.openflow.libopenflow_01 as of
from pox.lib.addresses import EthAddr, IPAddr
from pox.lib.packet.arp import arp

from gini.samples._l2 import L2

log = core.getLogger()

_ETH_IP = 0x0800
# Above a learned L2 rule (default 0x8000), so a reply to a client is always
# caught by the reverse rewrite even when a plain MAC rule for that client exists.
_LB_PRIORITY = 0x9000


class LoadBalancer(object):
    def __init__(self, connection, vip, vmac, backends):
        self.connection = connection
        self.vip = vip
        self.vmac = vmac
        self.backends = backends
        self.next = 0
        self.assigned = {}              # client IP -> backend IP
        self.l2 = L2(connection)        # delivers everything that is not VIP traffic
        connection.addListeners(self)
        for b in backends:              # learn the pool now, not on the first request
            self._probe(b)

    def _pick(self, client):
        if client not in self.assigned:
            if client in self.backends:
                log.warning("l4_lb: %s is in the backend pool but is sending to the VIP -- "
                            "check --backends against the addresses on each host", client)
            self.assigned[client] = self.backends[self.next % len(self.backends)]
            self.next += 1
        return self.assigned[client]

    def _probe(self, ip):
        # Asked AS the VIP, so a backend's answer comes back to the VIP's MAC and is learned
        # from there -- see _l2.L2.probe for why the other samples ask as 0.0.0.0 instead.
        self.l2.probe(ip, src_mac=self.vmac, src_ip=self.vip)

    def _handle_PacketIn(self, event):
        packet = event.parsed
        self.l2.learn(event)

        a = packet.find('arp')
        if a is not None:
            if a.opcode == arp.REQUEST and a.protodst == self.vip:
                self.l2.arp_reply(event, a, self.vmac, self.vip)
                return
            if packet.dst == self.vmac:     # a backend answering our probe: learned above
                return
            self.l2.forward(event, install=False)
            return

        ip = packet.find('ipv4')

        # Client -> VIP : choose a backend and rewrite the destination.
        if ip is not None and ip.dstip == self.vip:
            client = ip.srcip
            backend = self._pick(client)
            bmac, bport = self.l2.locate(backend)
            if bport is None:
                # Not learned yet (backend silent since start-up). Ask, and drop this one
                # packet -- TCP retransmits the SYN and the next one finds the backend.
                log.info("l4_lb: %s -> VIP -> %s (locating %s first)", client, backend, backend)
                self._probe(backend)
                return
            log.info("l4_lb: %s -> VIP -> %s", client, backend)

            fwd = of.ofp_flow_mod()
            fwd.priority = _LB_PRIORITY
            fwd.match = of.ofp_match(dl_type=_ETH_IP, nw_src=client, nw_dst=self.vip)
            fwd.idle_timeout = 30
            fwd.actions.append(of.ofp_action_dl_addr.set_dst(bmac))
            fwd.actions.append(of.ofp_action_nw_addr.set_dst(backend))
            fwd.actions.append(of.ofp_action_output(port=bport))
            fwd.data = event.ofp
            self.connection.send(fwd)

            rev = of.ofp_flow_mod()
            rev.priority = _LB_PRIORITY
            rev.match = of.ofp_match(dl_type=_ETH_IP, nw_src=backend, nw_dst=client)
            rev.idle_timeout = 30
            rev.actions.append(of.ofp_action_dl_addr.set_src(self.vmac))
            rev.actions.append(of.ofp_action_nw_addr.set_src(self.vip))
            rev.actions.append(of.ofp_action_output(port=event.port))
            self.connection.send(rev)
            return

        # Anything else: an ordinary learning switch. Installing is safe here -- the rules
        # above match on the VIP, whose MAC no host ever sends from, so no learned MAC rule
        # can carry VIP traffic past the controller.
        self.l2.forward(event, install=True)


class l4_lb(object):
    def __init__(self, vip, vmac, backends):
        self.vip = vip
        self.vmac = vmac
        self.backends = backends
        core.openflow.addListeners(self)

    def _handle_ConnectionUp(self, event):
        event.connection.send(of.ofp_flow_mod(command=of.OFPFC_DELETE))
        LoadBalancer(event.connection, self.vip, self.vmac, self.backends)
        log.info("gini.samples.l4_lb ready on %s (VIP %s -> %s)",
                 event.dpid, self.vip,
                 ", ".join(str(b) for b in self.backends))


def launch(vip="10.0.1.100", backends="10.0.1.11,10.0.1.12", vmac="02:00:00:00:01:64"):
    pool = [IPAddr(b) for b in backends.split(",")]
    core.registerNew(l4_lb, IPAddr(vip), EthAddr(vmac), pool)
