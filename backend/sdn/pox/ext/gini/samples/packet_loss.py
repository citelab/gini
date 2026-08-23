# gini.samples.packet_loss -- a controller that injects packet loss.
#
# It forwards traffic like a learning switch, but with probability LOSS it simply
# drops the frame instead of forwarding it. This is the SDN way to create a lossy
# link without touching any cable or qdisc: the loss lives in the control logic.
# Use it to watch how TCP throughput and ping behave as loss rises.
#
# Note it never installs a flow -- it forwards every frame from the controller
# with packet-out -- because a flow installed in the datapath would forward at
# line rate and the controller would lose its chance to drop anything. That makes
# this app a clean teaching tool, not a performance switch.
#
# Launch (drop ~30% of frames):
#   ./pox.py openflow.of_01 --port=6633 gini.samples.packet_loss --loss=0.3

import random
from pox.core import core
import pox.openflow.libopenflow_01 as of

log = core.getLogger()


class PacketLoss(object):
    def __init__(self, connection, loss):
        self.connection = connection
        self.loss = loss
        self.mac_to_port = {}
        self.dropped = 0
        self.forwarded = 0
        connection.addListeners(self)

    def _handle_PacketIn(self, event):
        packet = event.parsed
        self.mac_to_port[packet.src] = event.port

        if random.random() < self.loss:
            self.dropped += 1
            # No action sent -> the buffered packet is discarded by the switch.
            log.debug("drop %s -> %s (%d dropped / %d forwarded)",
                      packet.src, packet.dst, self.dropped, self.forwarded)
            return

        self.forwarded += 1
        out_port = self.mac_to_port.get(packet.dst, of.OFPP_FLOOD)
        msg = of.ofp_packet_out(data=event.ofp)
        msg.actions.append(of.ofp_action_output(port=out_port))
        self.connection.send(msg)


class packet_loss(object):
    def __init__(self, loss):
        self.loss = loss
        core.openflow.addListeners(self)

    def _handle_ConnectionUp(self, event):
        event.connection.send(of.ofp_flow_mod(command=of.OFPFC_DELETE))
        PacketLoss(event.connection, self.loss)
        log.info("gini.samples.packet_loss ready on %s (loss=%.0f%%)",
                 event.dpid, self.loss * 100)


def launch(loss="0.3"):
    core.registerNew(packet_loss, float(loss))
