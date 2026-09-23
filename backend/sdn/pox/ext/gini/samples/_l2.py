# gini.samples._l2 -- how the GINI sample apps forward "everything else".
#
# Not an app (hence the underscore): the samples that do something clever with SOME traffic --
# rewrite it, gate it, watch it -- still have to deliver the rest, and this is that half.
#
# It exists because every one of them used to deliver the rest with OFPP_NORMAL, which on the
# GINI Flow Switch (the gRouter in --openflow mode) does not mean what the OpenFlow spec's
# "behave like a normal switch" suggests. It means "hand the frame to the ROUTER's own IP
# pipeline". On a flat segment the router has no interfaces or routes to use, so NORMAL was a
# black hole: with any of those apps loaded, hosts could not even ARP for each other, and the
# lesson each app was written for never got a packet. This forwards the way gini.samples.switch
# does -- learn which port each MAC is on, flood what is unknown -- which is what NORMAL was
# standing in for.
#
# `install` is the one real choice, and it is per APP, not per packet:
#
#   install=True   put a dl_dst rule in the switch so the rest of the conversation never
#                  comes back to the controller. Right for a switch, and for l4_lb, whose own
#                  rules are matched on addresses no learned MAC rule can shadow.
#   install=False  send this one frame and install nothing. Right for any app that must SEE
#                  the next packet: a learned "dst = the server's MAC" rule would carry the
#                  next client's port-80 SYN past redirect, a knock past port_knock, and a
#                  scan past ids, and the app would silently stop working.

import pox.openflow.libopenflow_01 as of
from pox.lib.addresses import EthAddr, IPAddr
from pox.lib.packet.arp import arp
from pox.lib.packet.ethernet import ethernet

#: The source MAC of a probe the controller sends. Replies come back addressed to it, are
#: learned from, and are not forwarded anywhere.
PROBE_MAC = EthAddr("02:00:00:00:00:fe")
_ANY = IPAddr("0.0.0.0")
_BROADCAST = EthAddr("ff:ff:ff:ff:ff:ff")


class L2(object):
    def __init__(self, connection):
        self.connection = connection
        self.mac_to_port = {}           # MAC -> switch port
        self.ip_to_mac = {}             # IP -> MAC, from ARP and from IP sources

    def learn(self, event):
        """Call first, on every packet-in."""
        packet = event.parsed
        self.mac_to_port[packet.src] = event.port
        a = packet.find('arp')
        if a is not None and a.protosrc != _ANY:
            self.ip_to_mac[a.protosrc] = a.hwsrc
        ip = packet.find('ipv4')
        if ip is not None:
            self.ip_to_mac[ip.srcip] = packet.src

    def locate(self, ip):
        """(mac, port) for an address, or (None, None) if it has not been seen yet."""
        mac = self.ip_to_mac.get(ip)
        port = self.mac_to_port.get(mac) if mac is not None else None
        return (mac, port) if port is not None else (None, None)

    def forward(self, event, install=False):
        packet = event.parsed
        if packet.dst == PROBE_MAC:             # an answer to our own probe: learned, done
            return
        out = self.mac_to_port.get(packet.dst)
        if out is None or packet.dst.is_multicast:
            self.send(event.ofp, of.OFPP_FLOOD)
            return
        if not install:
            self.send(event.ofp, out)
            return
        msg = of.ofp_flow_mod()
        msg.match = of.ofp_match(dl_dst=packet.dst)
        msg.idle_timeout = 30
        msg.hard_timeout = 120
        msg.actions.append(of.ofp_action_output(port=out))
        msg.data = event.ofp
        self.connection.send(msg)

    def send(self, data, port, in_port=None):
        # Leave in_port alone for a frame that came from a packet-in: POX then copies the real
        # ingress port (and buffer) from it, which FLOOD needs so as not to send the frame back
        # where it came from. Passing OFPP_NONE there broke ARP between hosts outright. Only a
        # frame the controller BUILT names a port of its own.
        msg = (of.ofp_packet_out(data=data) if in_port is None
               else of.ofp_packet_out(data=data, in_port=in_port))
        msg.actions.append(of.ofp_action_output(port=port))
        self.connection.send(msg)

    def probe(self, ip, src_mac=PROBE_MAC, src_ip=_ANY):
        """Ask the segment who has `ip`; the reply is learned when it arrives.

        The default is an RFC 5227 probe -- sender IP 0.0.0.0 -- because it asks without
        CLAIMING an address. A probe sent as some real host would plant that host's IP against
        PROBE_MAC in the target's ARP cache, and a middlebox that forwards on to the real
        server would then send it nowhere. l4_lb passes its VIP instead, since there the whole
        point is to be answered at the VIP's MAC.
        """
        r = arp()
        r.opcode = arp.REQUEST
        r.hwsrc, r.protosrc = src_mac, src_ip
        r.hwdst, r.protodst = EthAddr("00:00:00:00:00:00"), ip
        e = ethernet(type=ethernet.ARP_TYPE, src=src_mac, dst=_BROADCAST)
        e.payload = r
        self.send(e.pack(), of.OFPP_FLOOD)

    def arp_reply(self, event, req, mac, ip):
        """Answer `req` as (`mac`, `ip`), out the port it came in on."""
        r = arp()
        r.opcode = arp.REPLY
        r.hwsrc, r.protosrc = mac, ip
        r.hwdst, r.protodst = req.hwsrc, req.protosrc
        e = ethernet(type=ethernet.ARP_TYPE, src=mac, dst=req.hwsrc)
        e.payload = r
        self.send(e.pack(), of.OFPP_IN_PORT, in_port=event.port)
