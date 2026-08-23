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
# Launch:
#   ./pox.py openflow.of_01 --port=6633 gini.samples.redirect \
#       --server=10.0.1.10 --port=80 --vnf=10.0.1.20

from pox.core import core
import pox.openflow.libopenflow_01 as of
from pox.lib.addresses import IPAddr

log = core.getLogger()

_ETH_IP = 0x0800
_IP_TCP = 6


class Redirect(object):
    def __init__(self, connection, server, port, vnf):
        self.connection = connection
        self.server = server            # original server IP
        self.port = port                # destination port to intercept
        self.vnf = vnf                  # middlebox IP to steer through
        connection.addListeners(self)

    def _handle_PacketIn(self, event):
        packet = event.parsed
        ip = packet.find('ipv4')
        tcp = packet.find('tcp')

        if ip is not None and tcp is not None \
                and ip.dstip == self.server and tcp.dstport == self.port:
            client = ip.srcip
            log.info("redirect: %s -> %s:%d steered to VNF %s",
                     client, self.server, self.port, self.vnf)

            fwd = of.ofp_flow_mod()
            fwd.match = of.ofp_match(dl_type=_ETH_IP, nw_proto=_IP_TCP,
                                     nw_src=client, nw_dst=self.server,
                                     tp_dst=self.port)
            fwd.idle_timeout = 30
            fwd.actions.append(of.ofp_action_nw_addr.set_dst(self.vnf))
            fwd.actions.append(of.ofp_action_output(port=of.OFPP_NORMAL))
            fwd.data = event.ofp
            self.connection.send(fwd)

            rev = of.ofp_flow_mod()
            rev.match = of.ofp_match(dl_type=_ETH_IP, nw_proto=_IP_TCP,
                                     nw_src=self.vnf, nw_dst=client,
                                     tp_src=self.port)
            rev.idle_timeout = 30
            rev.actions.append(of.ofp_action_nw_addr.set_src(self.server))
            rev.actions.append(of.ofp_action_output(port=of.OFPP_NORMAL))
            self.connection.send(rev)
            return

        msg = of.ofp_packet_out(data=event.ofp)
        msg.actions.append(of.ofp_action_output(port=of.OFPP_NORMAL))
        self.connection.send(msg)


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
