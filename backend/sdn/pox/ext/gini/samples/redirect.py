# gini.samples.redirect -- transparent redirect / VNF steering.
#
# Steer selected traffic to a middlebox without the client knowing. Any TCP
# traffic aimed at a given destination port (default 80) is redirected to a
# middlebox host (the VNF -- a proxy, cache, filter, or scrubber running in a
# container, see the NFV chapter). The controller rewrites the destination IP to
# the middlebox on the way in and restores the original server IP on the way back,
# so the client still believes it is talking to the original server.
#
# This is the SDN half of "service function chaining": the steering lives in flow
# rules, while the function itself lives in a container. Point --vnf at a machine
# running a proxy (for example python3 -m http.server, or a real caching proxy)
# and watch its logs light up while the clients are none the wiser.
#
# Two things the rewrite has to do that a sketch leaves out -- each was a way the
# redirect silently did nothing:
#
#   * REWRITE THE MAC, NOT JUST THE IP. The client addressed its frame to the server's
#     MAC. Rewriting only the IP still delivers the frame to the SERVER, which drops a
#     packet addressed to someone else. The forward rule sets dl_dst to the middlebox and
#     outputs to its port; the reverse rule puts the server's MAC back as the source.
#
#   * NEVER OFPP_NORMAL. On the GINI Flow Switch NORMAL is the router's IP pipeline, a
#     black hole on a flat segment -- see gini.samples._l2, which now delivers everything
#     else. It installs nothing: a learned "dst = the server's MAC" rule would carry the
#     NEXT client's port-80 SYN straight to the server, past the controller, and that
#     client would never be redirected.
#
# Launch:
#   ./pox.py openflow.of_01 --port=6633 gini.samples.redirect \
#       --server=10.0.1.10 --port=80 --vnf=10.0.1.20

from pox.core import core
import pox.openflow.libopenflow_01 as of
from pox.lib.addresses import IPAddr

from gini.samples._l2 import L2

log = core.getLogger()

_ETH_IP = 0x0800
_IP_TCP = 6
_STEER_PRIORITY = 0x9000        # above anything else the switch might hold


class Redirect(object):
    def __init__(self, connection, server, port, vnf):
        self.connection = connection
        self.server = server            # original server IP
        self.port = port                # destination port to intercept
        self.vnf = vnf                  # middlebox IP to steer through
        self.l2 = L2(connection)        # delivers everything that is not steered
        connection.addListeners(self)
        self.l2.probe(vnf)              # find the middlebox before the first client asks

    def _handle_PacketIn(self, event):
        packet = event.parsed
        self.l2.learn(event)
        ip = packet.find('ipv4')
        tcp = packet.find('tcp')

        if ip is not None and tcp is not None \
                and ip.dstip == self.server and tcp.dstport == self.port:
            client = ip.srcip
            vmac, vport = self.l2.locate(self.vnf)
            if vport is None:
                # The middlebox has not been heard from yet. Ask, and drop this SYN -- the
                # client retransmits it in a second, and the retry finds the middlebox.
                log.info("redirect: %s -> %s:%d (locating VNF %s first)",
                         client, self.server, self.port, self.vnf)
                self.l2.probe(self.vnf)
                return
            server_mac = packet.dst     # the client addressed the server; restore it on the way back
            log.info("redirect: %s -> %s:%d steered to VNF %s",
                     client, self.server, self.port, self.vnf)

            fwd = of.ofp_flow_mod()
            fwd.priority = _STEER_PRIORITY
            fwd.match = of.ofp_match(dl_type=_ETH_IP, nw_proto=_IP_TCP,
                                     nw_src=client, nw_dst=self.server,
                                     tp_dst=self.port)
            fwd.idle_timeout = 30
            fwd.actions.append(of.ofp_action_dl_addr.set_dst(vmac))
            fwd.actions.append(of.ofp_action_nw_addr.set_dst(self.vnf))
            fwd.actions.append(of.ofp_action_output(port=vport))
            fwd.data = event.ofp
            self.connection.send(fwd)

            rev = of.ofp_flow_mod()
            rev.priority = _STEER_PRIORITY
            rev.match = of.ofp_match(dl_type=_ETH_IP, nw_proto=_IP_TCP,
                                     nw_src=self.vnf, nw_dst=client,
                                     tp_src=self.port)
            rev.idle_timeout = 30
            rev.actions.append(of.ofp_action_dl_addr.set_src(server_mac))
            rev.actions.append(of.ofp_action_nw_addr.set_src(self.server))
            rev.actions.append(of.ofp_action_output(port=event.port))
            self.connection.send(rev)
            return

        self.l2.forward(event, install=False)


class redirect(object):
    def __init__(self, server, port, vnf):
        self.server = server
        self.port = port
        self.vnf = vnf
        core.openflow.addListeners(self)

    def _handle_ConnectionUp(self, event):
        event.connection.send(of.ofp_flow_mod(command=of.OFPFC_DELETE))
        Redirect(event.connection, self.server, self.port, self.vnf)
        log.info("gini.samples.redirect ready on %s (%s:%d -> VNF %s)",
                 event.dpid, self.server, self.port, self.vnf)


def launch(server="10.0.1.10", port="80", vnf="10.0.1.20"):
    core.registerNew(redirect, IPAddr(server), int(port), IPAddr(vnf))
