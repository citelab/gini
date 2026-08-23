# gini.samples.switch -- a learning switch for the GINI Flow Switch.
#
# This is the GINI default controller app. It behaves like an ordinary L2
# learning switch (learn which port a MAC lives on, then install a flow so the
# datapath forwards that pair on its own), with one GINI-specific twist: on
# ConnectionUp it first DELETES the match-all -> NORMAL rule that the GINI Flow
# Switch installs at start-up. Without that delete, the NORMAL rule would forward
# every frame in hardware and the controller would never see a packet-in, so the
# switch could never learn anything. Clearing it puts the datapath into a
# "table-miss -> controller" state, which is what a reactive controller needs.
#
# Launch:
#   ./pox.py openflow.of_01 --port=6633 gini.samples.switch

from pox.core import core
import pox.openflow.libopenflow_01 as of

log = core.getLogger()


class LearningSwitch(object):
    def __init__(self, connection):
        self.connection = connection
        self.mac_to_port = {}            # MAC -> switch port
        connection.addListeners(self)

    def _handle_PacketIn(self, event):
        packet = event.parsed
        self.mac_to_port[packet.src] = event.port   # learn the source

        out_port = self.mac_to_port.get(packet.dst)
        if out_port is None:
            # Unknown destination: flood this one frame and wait to learn.
            self._send(event, of.OFPP_FLOOD)
            return

        # Known destination: install a flow so the datapath forwards the rest of
        # this conversation without bothering the controller, then send this one.
        msg = of.ofp_flow_mod()
        msg.match = of.ofp_match.from_packet(packet, event.port)
        msg.idle_timeout = 30
        msg.hard_timeout = 120
        msg.actions.append(of.ofp_action_output(port=out_port))
        msg.data = event.ofp                         # send the buffered packet too
        self.connection.send(msg)
        log.debug("installed %s.%s -> port %s", packet.src, packet.dst, out_port)

    def _send(self, event, port):
        msg = of.ofp_packet_out(data=event.ofp)
        msg.actions.append(of.ofp_action_output(port=port))
        self.connection.send(msg)


class switch(object):
    def __init__(self):
        core.openflow.addListeners(self)

    def _handle_ConnectionUp(self, event):
        # Step out of the GINI default: drop the match-all -> NORMAL rule so we
        # start receiving packet-ins.
        event.connection.send(of.ofp_flow_mod(command=of.OFPFC_DELETE))
        LearningSwitch(event.connection)
        log.info("gini.samples.switch ready on datapath %s", event.dpid)


def launch():
    core.registerNew(switch)
