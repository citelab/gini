# gini.samples.ids -- a toy intrusion detector (port-scan spotter).
#
# The controller sees the first packet of every new conversation, which makes it
# a natural place to watch for scanning. This app counts how many distinct
# destination ports each source IP touches. When a source crosses THRESHOLD
# distinct ports it is flagged as a probable port scanner; with --block it also
# installs a drop flow so the scanner is cut off in the datapath.
#
# It is deliberately simple -- a real IDS would age its counters and look at
# rates, not just totals -- but it shows the shape of detection-in-the-controller
# and how a detection turns into an enforcement flow.
#
# Launch (detect only):
#   ./pox.py openflow.of_01 --port=6633 gini.samples.ids --threshold=10
# Launch (detect and block):
#   ./pox.py openflow.of_01 --port=6633 gini.samples.ids --threshold=10 --block=true

from pox.core import core
import pox.openflow.libopenflow_01 as of

log = core.getLogger()

_ETH_IP = 0x0800


class IDS(object):
    def __init__(self, connection, threshold, block):
        self.connection = connection
        self.threshold = threshold
        self.block = block
        self.ports_seen = {}            # src IP -> set of destination ports
        self.flagged = set()            # src IPs already reported
        connection.addListeners(self)

    def _handle_PacketIn(self, event):
        packet = event.parsed
        ip = packet.find('ipv4')
        l4 = packet.find('tcp') or packet.find('udp')
        if ip is not None and l4 is not None:
            seen = self.ports_seen.setdefault(ip.srcip, set())
            seen.add(l4.dstport)
            if len(seen) >= self.threshold and ip.srcip not in self.flagged:
                self.flagged.add(ip.srcip)
                log.warning("ids: possible port scan from %s (%d ports)",
                            ip.srcip, len(seen))
                if self.block:
                    self._block(ip.srcip)
                    return

        self._normal(event)

    def _block(self, src):
        msg = of.ofp_flow_mod()
        msg.match = of.ofp_match(dl_type=_ETH_IP, nw_src=src)
        msg.priority = 100
        msg.idle_timeout = 120
        # No actions == drop everything from this source for a while.
        self.connection.send(msg)
        log.warning("ids: blocking %s for 120s", src)

    def _normal(self, event):
        msg = of.ofp_packet_out(data=event.ofp)
        msg.actions.append(of.ofp_action_output(port=of.OFPP_NORMAL))
        self.connection.send(msg)


class ids(object):
    def __init__(self, threshold, block):
        self.threshold = threshold
        self.block = block
        core.openflow.addListeners(self)

    def _handle_ConnectionUp(self, event):
        event.connection.send(of.ofp_flow_mod(command=of.OFPFC_DELETE))
        IDS(event.connection, self.threshold, self.block)
        log.info("gini.samples.ids ready on %s (threshold=%d, block=%s)",
                 event.dpid, self.threshold, self.block)


def launch(threshold="10", block="false"):
    do_block = str(block).lower() in ("1", "true", "yes", "on")
    core.registerNew(ids, int(threshold), do_block)
