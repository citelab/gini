#!/usr/bin/python2

"""
firewall.py: Configures the switch to drop all IP packets directed to
192.168.1.3 except TCP packets with a destination port of 80.

This module takes a reactive approach by sending the packets to the controller
and making the modifications there by using flow table entries to make the
modifications.
"""

from pox.core import core
import pox.openflow.libopenflow_01 as of
from pox.lib.addresses import IPAddr

log = core.getLogger()

def send_packet(pkt, conn):
    msg = of.ofp_packet_out(data = pkt)
    msg.actions.append(of.ofp_action_output(port = of.OFPP_FLOOD))
    conn.send(msg)

def packet_in(event):
    packet = event.parsed
    ip = packet.find("ipv4")
    if ip is not None:
        ip_dst = ip.dstip
        if ip_dst == "192.168.1.3":
            tcp = packet.find("tcp")
            if tcp is not None:
                if tcp.dstport == 80:
                    send_packet(event.ofp, event.connection)

            return

    send_packet(event.ofp, event.connection)

def launch():
    core.openflow.addListenerByName("PacketIn", packet_in)
