# gini.samples.sfc -- VLAN-tag Service Function Chaining controller (GINI, OpenFlow 1.0).
#
# Steers a classified flow through an ORDERED chain of VNF ports, using a VLAN tag to carry
# the chain position (an NSH-like scheme; the gRouter-OVS supports SET_VLAN_VID / STRIP_VLAN).
# The idea:
#   - a packet matching the classifier enters at the ingress port -> push VLAN (base+1),
#     output to the first VNF's port;
#   - a packet coming back from VNF i still carries VLAN (base+i) -> rewrite to (base+i+1)
#     and output to VNF i+1's port;
#   - after the last VNF -> strip the VLAN and output to the egress port.
#
# Configure via environment (the orchestrator passes these per topology):
#   SFC_CHAIN     = comma-separated OpenFlow port numbers of the VNFs, in order   e.g. "2,3"
#   SFC_INGRESS   = the port classified traffic arrives on                        e.g. "1"
#   SFC_EGRESS    = the port to send to after the last VNF                        e.g. "4"
#   SFC_VLAN_BASE = base VLAN id for this chain (default 100)
#
# With NO SFC_CHAIN it falls back to a plain L2 learning switch, so the controller is always
# safe to run. NOTE: tag-based steering requires the VNFs to PRESERVE the frame+tag
# (L2-transparent bump-in-the-wire); an L3-forwarding VNF strips the tag -- see NFV_SFC_DESIGN.
#
# Launch:  ./pox.py openflow.of_01 --port=6633 gini.samples.sfc

import os

import pox.openflow.libopenflow_01 as of
from pox.core import core

log = core.getLogger()


def _ports(env):
    return [int(p) for p in os.environ.get(env, "").split(",") if p.strip().isdigit()]


def _port(env, default=None):
    v = os.environ.get(env, "")
    return int(v) if v.strip().isdigit() else default


class _LearningSwitch(object):
    """Safe fallback: a reactive L2 learning switch (same as gini.samples.switch)."""
    def __init__(self, connection):
        self.connection = connection
        self.mac_to_port = {}
        connection.addListeners(self)

    def _handle_PacketIn(self, event):
        packet = event.parsed
        self.mac_to_port[packet.src] = event.port
        out = self.mac_to_port.get(packet.dst)
        if out is None:
            msg = of.ofp_packet_out(data=event.ofp)
            msg.actions.append(of.ofp_action_output(port=of.OFPP_FLOOD))
            self.connection.send(msg)
            return
        msg = of.ofp_flow_mod()
        msg.match = of.ofp_match.from_packet(packet, event.port)
        msg.idle_timeout = 30
        msg.actions.append(of.ofp_action_output(port=out))
        msg.data = event.ofp
        self.connection.send(msg)


class SFC(object):
    """Installs the VLAN-tag steering chain proactively when the switch connects."""
    def __init__(self):
        self.chain = _ports("SFC_CHAIN")
        self.ingress = _port("SFC_INGRESS", 1)
        self.egress = _port("SFC_EGRESS")
        self.vbase = _port("SFC_VLAN_BASE", 100)
        core.openflow.addListeners(self)

    def _flow(self, match, actions, priority=200):
        msg = of.ofp_flow_mod()
        msg.priority = priority
        msg.match = match
        for a in actions:
            msg.actions.append(a)
        return msg

    def _handle_ConnectionUp(self, event):
        conn = event.connection
        conn.send(of.ofp_flow_mod(command=of.OFPFC_DELETE))     # clear the default
        if not self.chain:
            _LearningSwitch(conn)                                # no chain -> plain switch
            log.info("gini.samples.sfc: no SFC_CHAIN -> learning switch on %s", event.dpid)
            return

        # 1) classify: traffic arriving on the ingress port, untagged -> enter the chain.
        m = of.ofp_match(); m.in_port = self.ingress
        conn.send(self._flow(m, [of.ofp_action_vlan_vid(vlan_vid=self.vbase + 1),
                                 of.ofp_action_output(port=self.chain[0])]))

        # 2) walk: a frame returning from VNF i (tag base+i+1, in-port chain[i]) -> next hop.
        for i, port in enumerate(self.chain):
            m = of.ofp_match(); m.in_port = port; m.dl_vlan = self.vbase + i + 1
            last = i == len(self.chain) - 1
            if last:
                acts = [of.ofp_action_strip_vlan()]
                if self.egress is not None:
                    acts.append(of.ofp_action_output(port=self.egress))
                else:
                    acts.append(of.ofp_action_output(port=of.OFPP_NORMAL))
            else:
                acts = [of.ofp_action_vlan_vid(vlan_vid=self.vbase + i + 2),
                        of.ofp_action_output(port=self.chain[i + 1])]
            conn.send(self._flow(m, acts))

        log.info("gini.samples.sfc: chain %s ingress=%s egress=%s vbase=%s on %s",
                 self.chain, self.ingress, self.egress, self.vbase, event.dpid)


def launch():
    core.registerNew(SFC)
