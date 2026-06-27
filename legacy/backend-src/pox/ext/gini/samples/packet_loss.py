#!/usr/bin/python2

"""
packet_loss.py: Simulates packet loss by dropping all packets with a
probability of 25%.
"""

import random

from pox.core import core
import pox.openflow.libopenflow_01 as of

def packet_in(event):
    if random.random() >= 0.25:
        msg = of.ofp_packet_out(data = event.ofp)
        msg.actions.append(of.ofp_action_output(port = of.OFPP_FLOOD))
        event.connection.send(msg)

def launch():
    core.openflow.addListenerByName("PacketIn", packet_in)
