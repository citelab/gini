# gini.samples.ids -- a toy intrusion detector (port-scan spotter).
#
# Every packet passes through the controller (nothing is installed to carry traffic
# past it), which makes it a natural place to watch for scanning. This app counts how
# many distinct destination ports each source IP tries to OPEN -- TCP SYNs, and UDP
# below the ephemeral range; replies are not attempts (see _is_probe). When a source
# crosses THRESHOLD distinct ports it is flagged as a probable port scanner; with
# --block it also installs a drop flow so the scanner is cut off in the datapath.
#
# It is deliberately simple -- a real IDS would age its counters and look at
# rates, not just totals -- but it shows the shape of detection-in-the-controller
# and how a detection turns into an enforcement flow.
#
# Traffic is delivered by gini.samples._l2 and NOT installed as a flow, deliberately: the
# detector only sees what reaches the controller, and a learned "dst = the target's MAC" rule
# would carry the rest of a scan past it. (It used to be sent out OFPP_NORMAL, which on the
# GINI Flow Switch is the router's IP pipeline and delivered nothing on a flat segment -- so
# the network simply did not work with the IDS on, and there was never a scan to see.)
#
# Launch (detect only):
#   ./pox.py openflow.of_01 --port=6633 gini.samples.ids --threshold=10
# Launch (detect and block):
#   ./pox.py openflow.of_01 --port=6633 gini.samples.ids --threshold=10 --block=true

from pox.core import core
import pox.openflow.libopenflow_01 as of

from gini.samples._l2 import L2

log = core.getLogger()

_ETH_IP = 0x0800


class IDS(object):
    def __init__(self, connection, threshold, block):
        self.connection = connection
        self.threshold = threshold
        self.block = block
        self.ports_seen = {}            # src IP -> set of destination ports
        self.flagged = set()            # src IPs already reported
        self.l2 = L2(connection)        # delivers traffic, installs nothing
        connection.addListeners(self)

    def _handle_PacketIn(self, event):
        packet = event.parsed
        self.l2.learn(event)
        ip = packet.find('ipv4')
        l4 = packet.find('tcp') or packet.find('udp')
        if ip is not None and l4 is not None and self._is_probe(l4):
            seen = self.ports_seen.setdefault(ip.srcip, set())
            seen.add(l4.dstport)
            if len(seen) >= self.threshold and ip.srcip not in self.flagged:
                self.flagged.add(ip.srcip)
                log.warning("ids: possible port scan from %s (%d ports)",
                            ip.srcip, len(seen))
                if self.block:
                    self._block(ip.srcip)
                    return

        self.l2.forward(event)

    def _is_probe(self, l4):
        """Does this packet ASK for a port, rather than answer from one?

        Counting every packet flagged the victim instead of the scanner: each of a client's
        connections comes from a fresh ephemeral port, so a web server's REPLIES touch a new
        destination port every time, and ten ordinary page loads made the server a "port
        scanner" -- blocked, and taking the site down with it. A scan is a string of attempts,
        so count attempts: a TCP SYN without ACK, and UDP aimed below the ephemeral range
        (32768+ is where Linux sends replies from and to).
        """
        if l4.__class__.__name__ == 'tcp':
            return l4.SYN and not l4.ACK
        return l4.dstport < 32768

    def _block(self, src):
        msg = of.ofp_flow_mod()
        msg.match = of.ofp_match(dl_type=_ETH_IP, nw_src=src)
        msg.priority = 100
        msg.idle_timeout = 120
        # No actions == drop everything from this source for a while.
        self.connection.send(msg)
        log.warning("ids: blocking %s for 120s", src)



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
