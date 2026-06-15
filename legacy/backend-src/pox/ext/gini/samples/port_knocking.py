#!/usr/bin/python2

from pox.core import core
import pox.openflow.libopenflow_01 as of
from pox.lib.addresses import IPAddr

log = core.getLogger()

"""
port_knocking.py: Configures the switch to drop all TCP packets directed to
172.31.0.5 with a destination port of 23 unless a set of TCP packets are
received with the following ports in that order: 12299, 13574, and 48293.

Initially, the switch is configured to send all TCP packets directed to
172.31.0.5 to the controller. Once the requirements above are met, this rule
is removed to improve performance on the switch.
"""

blocked = True
blocked_port = 23

port_seq = [12299, 13574, 48293]
port_seq_index = 0

def send_packet(pkt, conn):
    msg = of.ofp_packet_out(data = pkt)
    msg.actions.append(of.ofp_action_output(port = of.OFPP_FLOOD))
    conn.send(msg)

def packet_in(event):
    global blocked
    global port_seq_index

    packet = event.parsed
    tcp = packet.find("tcp")

    if blocked:
        if tcp.dstport == port_seq[port_seq_index]:
            port_seq_index += 1
            log.info("Port sequence index: " + str(port_seq_index) + " (len: " +
                str(len(port_seq)) + ")")
        elif port_seq_index == 0 or tcp.dstport == port_seq[port_seq_index - 1]:
            # Don't reset the port knocker if we receive multiple packets for
            # the current port
            pass
        else:
            port_seq_index = 0
            log.info("Port sequence index: " + str(port_seq_index) + " (len: " +
                str(len(port_seq)) + ")")

        if port_seq_index == len(port_seq):
            log.info("Unblocking blocked port")
            blocked = False

            msg = of.ofp_flow_mod()
            msg.command = of.OFPFC_DELETE
            msg.priority = 100
            msg.match.dl_type = 0x800
            msg.match.nw_proto = 6
            msg.match.nw_dst = IPAddr("172.31.0.5")
            event.connection.send(msg)

    if tcp.dstport == blocked_port:
        if not blocked:
            send_packet(event.ofp, event.connection)
    else:
        send_packet(event.ofp, event.connection)

def connection_up(event):
    msg = of.ofp_flow_mod()
    msg.priority = 50
    msg.actions.append(of.ofp_action_output(port = of.OFPP_FLOOD))
    event.connection.send(msg)

    msg = of.ofp_flow_mod()
    msg.priority = 100
    msg.match.dl_type = 0x800
    msg.match.nw_proto = 6
    msg.match.nw_dst = IPAddr("172.31.0.5")
    msg.actions.append(of.ofp_action_output(port = of.OFPP_CONTROLLER))
    event.connection.send(msg)

def launch():
    core.openflow.addListenerByName("ConnectionUp", connection_up)
    core.openflow.addListenerByName("PacketIn", packet_in)
