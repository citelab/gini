#!/usr/bin/python2

"""
icmp_proxy.py: Configures the switch to create a very simple ICMP echo proxy 
for requests directed to 172.31.0.3 from 172.31.0.2. Requests are instead
redirected to 172.31.0.4.
   
This module takes a proactive approach by using flow table entries to make the
modifications rather than sending the packets to the controller and making the
modifications there.
"""

from pox.core import core
import pox.openflow.libopenflow_01 as of
from pox.lib.addresses import IPAddr, EthAddr

def connection_up(event):
    msg = of.ofp_flow_mod()
    msg.priority = 50
    msg.actions.append(of.ofp_action_output(port = of.OFPP_FLOOD))
    event.connection.send(msg)

    msg = of.ofp_flow_mod()
    msg.priority = 100
    msg.match.dl_type = 0x800
    msg.match.nw_proto = 1
    msg.match.nw_src = IPAddr("172.31.0.2")
    msg.match.nw_dst = IPAddr("172.31.0.3")
    msg.match.tp_src = 8
    msg.actions.append(of.ofp_action_dl_addr.set_dst(
        EthAddr("fe:fd:02:00:00:03")))
    msg.actions.append(of.ofp_action_nw_addr.set_dst(
        IPAddr("172.31.0.4")))
    msg.actions.append(of.ofp_action_output(port = of.OFPP_FLOOD))
    event.connection.send(msg)

    msg = of.ofp_flow_mod()
    msg.priority = 100
    msg.match.dl_type = 0x800
    msg.match.nw_proto = 1
    msg.match.nw_src = IPAddr("172.31.0.4")
    msg.match.nw_dst = IPAddr("172.31.0.2")
    msg.match.tp_src = 0
    msg.actions.append(of.ofp_action_dl_addr.set_src(
        EthAddr("fe:fd:02:00:00:02")))
    msg.actions.append(of.ofp_action_nw_addr.set_src(
        IPAddr("172.31.0.3")))
    msg.actions.append(of.ofp_action_output(port = of.OFPP_FLOOD))
    event.connection.send(msg)

def launch():
    core.openflow.addListenerByName("ConnectionUp", connection_up)
