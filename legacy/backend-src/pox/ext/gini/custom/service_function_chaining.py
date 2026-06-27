#!/usr/bin/python2.7

"""This component is responsible for interacting with a simple-cloud
manager to figure out which service function chain is set up and
their details to install rules on relevant OpenvSwitch.

It does that by communicating with the simple-cloud manager through a
north-bound interface (via unix domain socket or regular socket). When
a service function is brought up by the manager, this component tries
to retrieve the service's IP addresses and set up corresponding flow
rules. In case no service function is required, it will fall back to
using forwarding.l2_pairs component.
"""

from pox.core import core
import pox.openflow.libopenflow_01 as of
from pox.lib import addresses as libaddr
from pox.lib.packet.ethernet import ethernet, ETHER_ANY, ETHER_BROADCAST
from pox.lib.packet.ipv4 import ipv4, IP_ANY, IP_BROADCAST
from pox.lib.packet.arp import arp
import pox.lib.util as poxutil
import json
from socket import *
import threading
import random
from enum import IntEnum

log = core.getLogger()

# table of (switch, MAC) -> port, similar to L2 pairs
l2_pairs_table = {}
all_ports = of.OFPP_FLOOD

# SDN Controller will listen to this port for instructions from the SFC orchestrator
NORTHBOUND_PORT = 8081
BUFFER_SIZE = 4096


class MessageType(IntEnum):
    META = 0
    CONTROL = 1


class MetaAction(IntEnum):
    HELLO = 0
    BYE = 1


class ControlAction(IntEnum):
    ADD_CHAIN = 0
    DEL_CHAIN = 1
    ADD_PATH = 2
    DEL_PATH = 3


class ControllerMessage:
    def __init__(self, dpid, controllerIP):
        self._type = None
        self._action = None
        self._controllerIP = controllerIP
        self._dpid = dpid
        self.src = None
        self.dst = None
        self.chainID = -1
        self.chain = []

    def setType(self, t):
        if t in MessageType:
            self._type = t
            return True
        return False

    def setAction(self, action):
        if self._type == MessageType.META:
            if action in MetaAction:
                self._action = action
                return True
            return False
        elif self._type == MessageType.CONTROL:
            if action in ControlAction:
                self._action = action
                return True
            return False
        else:
            return False


def _randomMacAddress():
    macAddr = "02:00:00:%02x:%02x:%02x" % (random.randint(0, 255),
                                            random.randint(0, 255),
                                            random.randint(0, 255))
    return libaddr.EthAddr(macAddr)


class Node:
    """
    This class represents a single node in the SFC-enabled network topology. In
    this case, a node is uniquely identified by its IP address, which is reach
    -able from other nodes in the same network. A node can be client, server
    machine, or network function service, etc.

    Since each node in the network is connected to an OpenvSwitch, sometimes it
    might be useful to locate exactly which OVS port the node is connected to.
    Therefore, this class provides a function to discover the OVS port, which
    can then be called by the SDN controller to perform active discovery.
    """
    def __init__(self, ipaddr, macaddr, port=None):
        self.ip = ipaddr
        self.mac = macaddr
        self.port = -1 if port is None else port
        self._ovsPort = None

    def setOvsPort(self, port):
        self._ovsPort = port

    def getOvsPort(self):
        return self._ovsPort

    def __hash__(self):
        return hash(self.ip)

    def __str__(self):
        return '%s:%s. Device connected to port %s' % (self.ip,
                                                        self.port,
                                                        self._ovsPort)

    def __repr__(self):
        return '<Node: %s:%s>' % (self.ip, self.port)


class ServiceFunctionChain:
    """
    This class represents a service function chain. A service function chain is
    an ordered set of network services like firewall, IPS, load balancer, etc.
    In the case of Gini network, a service chain is uniquely identified by the
    SFC-enabled private network and its index. Since all service nodes of the
    chain is connected to a single OpenvSwitch, the whole chain will use a tuple
    of (OVS' DPID, Chain index) as its ID.

    Attributes:
        services:   list of network services (Node objects)
        _id:        a special string which uniquely identifies this SFC
        portMap:    Mapping of OVS port -> index of node

    Note that a single network Node may belong to more than one service chain.
    """
    SOURCE_NODE = -1
    DESTINATION_NODE = -2

    def __init__(self, services, serviceID):
        self.services = services
        self._id = serviceID
        self.portMap = {}

        for i, node in enumerate(self.services):
            self.portMap[node.getOvsPort()] = i

        self.firstNode = 0
        self.lastNode = len(self.services)-1

    def getServiceID(self):
        return self._id

    def getNextNodeIndex(self, ovsPort):
        """
        Given ingress port number of the current packet, we need to figure out
        where it should go next
        """
        idx = self.portMap.get(ovsPort, self.SOURCE_NODE)
        if idx == self.SOURCE_NODE:
            return self.firstNode
        elif idx == self.lastNode:
            return self.DESTINATION_NODE
        else:
            return idx+1

    def __hash__(self):
        return hash(self._id)

    def __str__(self):
        chainString = '-'.join([repr(s) for s in self.services])
        return 'Service chain of: {%s}' % chainString

    def __repr__(self):
        return '<ServiceFunctionChain: %s>' % (self._id)


class OvsConnection:
    """
    This class represents an OVS connection to the SDN controller. Although we
    can use inheritance to extend the existing Connection object, it's easier
    to just hold a connection object as an attribute of this class.

    Attributes:
        conn:       the connection object, used to communicate with the OVS
        ip:         the reserved IP address that the controller can use to send
                    messages to hosts in the network
        nodes:      Map of IPv4 -> Node, representing hosts in the network
        servicePaths:
        availableChains:
        _macAddress:
    """
    def __init__(self, conn, ipaddr=None):
        self.conn = conn

        # Reserved IP address for the SDN controller.
        if ipaddr:
            self._ip = libaddr.IPAddr(ipaddr)
        else:
            self._ip = IP_ANY   # we can update this later
        self._macAddress = _randomMacAddress()

        # relevant network nodes connected to this OVS
        self.nodes = {}

        # available service chains reachable by the OVS
        self.availableChains = {}

        # full SFC path between 2 endpoints. Each path is identified by an ordered
        # pair of the endpoints. Service chains can be reused by different pairs.
        # This table will be filled after active discovery by the SDN controller
        self.servicePaths = {}

    def setIP(self, ipstring):
        """
        Set an IP address for the SDN controller between this connection
        """
        ipaddr = libaddr.IPAddr(ipstring)
        self._ip = ipaddr

    def getIP(self):
        return self._ip

    def getMAC(self):
        return self._macAddress

    @property
    def dpid(self):
        return self.conn.dpid

    def installArpFlow(self):
        """
        Install ARP flow on a switch. Every ARP response that is targeted to
        self._macAddress will be forwarded to the controller.
        """
        msg = of.ofp_flow_mod()
        msg.priority += 1
        msg.match.dl_type = ethernet.ARP_TYPE
        msg.match.dl_dst = self._macAddress
        # msg.match.nw_dst = self._ip

        msg.actions.append(of.ofp_action_output(port=of.OFPP_CONTROLLER))
        self.conn.send(msg)

    def addNode(self, ip, mac, port=None):
        """
        Construct a new Node from the given argument, keep it in self.nodes,
        and perform active OVS port discovery. If there is already a node with
        the same IP and MAC, no new node will be created
        """
        ipaddr = libaddr.IPAddr(ip)
        macaddr = libaddr.EthAddr(mac)

        node = self.nodes.get(ipaddr, None)
        if node and node.mac == macaddr:
            return node

        newNode = Node(ipaddr, macaddr, port=port)
        self.nodes[ipaddr] = newNode
        self.sendArpRequest(newNode)

        log.debug('New Node object created with IP %s' % ipaddr)

        return newNode

    def sendArpRequest(self, node):
        """
        Send an ARP request to node's IP, then expects an ARP reply to come in
        from some ingress port. The handler will then save the node's port.
        """
        r = arp()
        r.opcode = arp.REQUEST
        r.hwsrc = self._macAddress
        r.hwdst = ETHER_BROADCAST
        r.protosrc = self._ip
        r.protodst = node.ip

        e = ethernet(type=ethernet.ARP_TYPE, src=r.hwsrc, dst=r.hwdst)
        e.payload = r

        msg = of.ofp_packet_out(
            data=e.pack(),
            action=of.ofp_action_output(port=all_ports)
        )

        self.conn.send(msg)

    def addServiceChain(self, serviceChain):
        """
        Append a new service chain to the list of available chains
        """
        serviceID = serviceChain.getServiceID()
        self.availableChains[serviceID] = serviceChain

        log.debug('Added a new service chain %s' % serviceChain)

    def removeServiceChain(self, chainID):
        """
        Remove an available service chain
        """
        if chainID not in self.availableChains:
            return

        pathToRemove = []
        for pathID, chain in self.servicePaths.items():
            if chainID == chain.getServiceID():
                pathToRemove.append(pathID)
        for t in pathToRemove:
            srcIP, dstIP = t
            src = self.nodes.get(srcIP)
            dst = self.nodes.get(dstIP)
            self.removeServicePath(src, dst)

        del self.availableChains[chainID]

        log.debug('Removed service chain with ID %s' % chainID)

    def addServicePath(self, src, dst, serviceChainID):
        """
        Append a new service path, uniquely identified by the (src,dst) pair.
        If serviceID does not exist in availableServiceChains, do nothing.
        """
        serviceChain = self.availableChains.get(serviceChainID)
        if not serviceChain:
            log.warning('No service chain with ID %s found' % str(serviceChainID))
        else:
            self.servicePaths[(src.ip,dst.ip)] = serviceChain
            self.installServiceChainFlow(src.ip, dst.ip, serviceChain)

            log.debug('Created service path between %s and %s' % (src.ip, dst.ip))

    def removeServicePath(self, src, dst):
        """
        Remove an existing service path
        """
        pathID = (src.ip, dst.ip)
        chain = self.servicePaths.get(pathID, -1)
        if chain.getServiceID() == -1:
            log.warning('No service path with src and dst found')
        else:
            self.removeServiceChainFlow(src.ip, dst.ip, chain)
            del self.servicePaths[pathID]

            log.debug('Removed service path between %s and %s' % (src.ip, dst.ip))

    def setNodeOvsPort(self, nodeIP, ovsPort):
        """
        Set OVS port for node with address nodeIP
        """
        node = self.nodes.get(nodeIP, None)
        if not node:
            return False
        node.setOvsPort(ovsPort)
        return True

    def sendMessage(self, msg):
        """
        Send message to the connected OVS
        """
        self.conn.send(msg)

    def installServiceChainFlow(self, srcIP, dstIP, chain, ofp=None):
        """
        Install flow rules for the whole service path from source to destination
        """
        log.info('Installing flow rules for chain from %s -> %s' % (srcIP,dstIP))

        srcNode = self.nodes.get(srcIP, None)
        dstNode = self.nodes.get(dstIP, None)

        servicePath = [srcNode] + chain.services + [dstNode]

        pendingMessages = []

        for idx in range(len(servicePath)-1):
            curNode = servicePath[idx]
            nextNode = servicePath[idx+1]
            curPort = curNode.getOvsPort()
            nextPort = nextNode.getOvsPort()

            if curPort is None or nextPort is None:
                return

            msg = of.ofp_flow_mod()
            msg.data = ofp if ofp else None
            msg.priority += 1
            msg.match.dl_type = ethernet.IP_TYPE
            msg.match.nw_src = srcIP
            msg.match.nw_dst = dstIP
            msg.match.in_port = curPort
            msg.hard_timeout = 120
            msg.actions.append(of.ofp_action_dl_addr.set_dst(nextNode.mac))
            msg.actions.append(of.ofp_action_output(port=nextPort))

            pendingMessages.append(msg)

        # Send all after message construction
        for msg in pendingMessages:
            self.sendMessage(msg)

    def removeServiceChainFlow(self, srcIP, dstIP, chain):
        """
        Remove flow rules in the OVS corresponding to a service path
        """
        log.info('Removing flow rules for chain from %s -> %s' % (srcIP,dstIP))

        srcNode = self.nodes.get(srcIP, None)
        dstNode = self.nodes.get(dstIP, None)

        servicePath = [srcNode] + chain.services + [dstNode]

        pendingMessages = []

        for idx in range(len(servicePath)-1):
            curNode = servicePath[idx]
            nextNode = servicePath[idx+1]
            curPort = curNode.getOvsPort()
            nextPort = nextNode.getOvsPort()

            if curPort is None or nextPort is None:
                return

            msg = of.ofp_flow_mod(command=of.OFPFC_DELETE)
            msg.match.dl_type = ethernet.IP_TYPE
            msg.match.nw_src = srcIP
            msg.match.nw_dst = dstIP
            msg.match.in_port = curPort

            pendingMessages.append(msg)

        for msg in pendingMessages:
            self.sendMessage(msg)

    def __str__(self):
        return str(self.dpid)

    def __repr__(self):
        return "<OvsConnection: %d>" % self.dpid


class SfcController:
    """
    This is the actual component that's handling all connections and PacketIn
    events related to service function chains.

    TODO: update this docstring
    """
    _core_name = 'sfc'

    def __init__(self):
        # holds DPIDs and actual Connection objects
        self.connections = {}

        for conn in core.openflow.connections:
            ovsConn = OvsConnection(conn)
            self.connections[conn.dpid] = ovsConn
            ovsConn.installArpFlow()

        core.openflow.addListeners(self)

        # listen via NorthBound API
        t = threading.Thread(target=self.listen)
        t.daemon = True
        t.start()

    def _handle_ConnectionUp(self, event):
        """
        On ConnectionUp event, install a rule on the controller to forward all
        ARP packet with a specific destination MAC address to the controller.
        This is used for service discovery in the controller.
        """
        log.debug('Installing flow rule for ARP ping responses')

        # save this connection object and DPID
        ovsConn = OvsConnection(event.connection)
        self.connections[event.dpid] = ovsConn

        # install ARP flow rule
        ovsConn.installArpFlow()

    def _handle_ConnectionDown(self, event):
        """
        When a connection is terminated due to timeout, remove the corresponding
        connection object. Everything else (service chain, etc) can be get rid
        of lazily.
        """
        self.connections.pop(event.dpid, None)

    def _l2Pairs_PacketIn_handle(self, event):
        """
        Code from forwarding.l2_pairs module. Here we add a timeout parameter
        so that it's possible to install flow rules for SFC later on
        """
        packet = event.parsed

        l2_pairs_table[(event.connection,packet.src)] = event.port
        dst_port = l2_pairs_table.get((event.connection,packet.dst))

        if dst_port is None:
            # Flood the packet to find the destination port first
            msg = of.ofp_packet_out(data=event.ofp)
            msg.actions.append(of.ofp_action_output(port=all_ports))
            event.connection.send(msg)
        else:
            # Install flow rule for one way
            msg = of.ofp_flow_mod()
            msg.match.dl_dst = packet.src
            msg.match.dl_src = packet.dst
            msg.hard_timeout = 10
            msg.actions.append(of.ofp_action_output(port=event.port))
            event.connection.send(msg)

            # Flow rule for the other way, also forward the packet
            msg = of.ofp_flow_mod()
            msg.data = event.ofp
            msg.match.dl_src = packet.src
            msg.match.dl_dst = packet.dst
            msg.hard_timeout = 10
            msg.actions.append(of.ofp_action_output(port=dst_port))
            event.connection.send(msg)

            log.debug("Installing %s <-> %s" % (packet.src, packet.dst))

    def _arp_PacketIn_handle(self, event):
        """
        This function is called only when an ARP packet with the destination
        equal to self._macAddress arrives at the controller. This is the ARP
        response from one of the nodes in the network. Only after this reply
        can the OVS port of the node be resolved.
        """
        packet = event.parsed
        srcIP = packet.payload.protosrc
        dstIP = packet.payload.protodst
        inPort = event.port
        conn = self.connections[event.dpid]

        if packet.dst == conn.getMAC():
            log.info('Controller received an ARP reply')

            if conn.setNodeOvsPort(srcIP, inPort):
                log.debug('Host %s is connected to port %d' % (srcIP, inPort))
            else:
                log.debug('Ignore ARP reply from %s' % srcIP)
        else:
            self._l2Pairs_PacketIn_handle(event)

    def _sfc_PacketIn_handle(self, event, ipPacket):
        """
        Get the matching service chain according to the source-destination
        IP addresses. Based on the ingress port, send the packet to the
        next service in the chain, and also install a rule in the switch.
        """
        conn = self.connections[event.dpid]
        src = ipPacket.srcip
        dst = ipPacket.dstip
        inPort = event.port

        serviceChain = conn.servicePaths.get((src,dst))
        nodeIndex = serviceChain.getNextNodeIndex(inPort)
        if nodeIndex == ServiceFunctionChain.DESTINATION_NODE:
            nextNode = conn.nodes.get(dst)
        else:
            nextNode = serviceChain.services[nodeIndex]
        outPort = nextNode.getOvsPort()

        log.info('Next forward destination: %s' % nextNode)
        log.info('Output to port %d' % outPort)

        # install flow rules in addition to sending packet out
        conn.installServiceChainFlow(src, dst, serviceChain, event.ofp)

    def _handle_PacketIn(self, event):
        """
        Investigate the source and destination addresses or ingress port. If
        there exists a service function chain corresponding to the source and
        destination, call _sfc_PacketIn_handle(). Otherwise, use L2_pairs.
        """
        packet = event.parsed
        conn = self.connections[event.dpid]

        ip = packet.find('ipv4')

        if ip:
            src = ip.srcip
            dst = ip.dstip
            if (src,dst) in conn.servicePaths:
                self._sfc_PacketIn_handle(event, ip)
            else:       # default handler is there's no matching SFC
                self._l2Pairs_PacketIn_handle(event)
        else:
            if packet.type == ethernet.ARP_TYPE:
                self._arp_PacketIn_handle(event)
            else:
                self._l2Pairs_PacketIn_handle(event)

    def controllerMessageHandler(self, message):
        try:
            parsed = json.loads(message)

            msgType = parsed.get("Type", None)
            msgAction = parsed.get("Action", None)
            dpid = parsed.get("DPID")
            poxIP = parsed.get("POX IP")
            src = parsed.get("Source")
            dst = parsed.get("Destination")
            chainID = parsed.get("ChainID")
            input_chain = parsed.get("Chain")
            ovsConn = self.connections.get(dpid, None)

            if not ovsConn:
                return

            msgType = MessageType(msgType)

            if msgType == MessageType.META:
                msgAction = MetaAction(msgAction)

                if msgAction == MetaAction.HELLO:
                    ovsConn.setIP(poxIP)
                else:
                    return True
            elif msgType == MessageType.CONTROL:
                msgAction = ControlAction(msgAction)

                if msgAction == ControlAction.ADD_CHAIN:
                    services = []
                    for interface in input_chain:
                        ip, mac = interface['IP'], interface['MAC']
                        newNode = ovsConn.addNode(ip, mac)
                        services.append(newNode)
                        chain = ServiceFunctionChain(services, chainID)
                        ovsConn.addServiceChain(chain)
                elif msgAction == ControlAction.DEL_CHAIN:
                    ovsConn.removeServiceChain(chainID)
                elif msgAction == ControlAction.ADD_PATH:
                    source = ovsConn.addNode(src['IP'], src['MAC'])
                    destination = ovsConn.addNode(dst['IP'], dst['MAC'])
                    ovsConn.addServicePath(source, destination, chainID)
                elif msgAction == ControlAction.DEL_PATH:
                    source = ovsConn.addNode(src['IP'], src['MAC'])
                    destination = ovsConn.addNode(dst['IP'], dst['MAC'])
                    ovsConn.removeServicePath(source, destination)
        except Exception as err:
            log.exception(err)

    def clientThread(self, client, addr):
        log.debug('New client connected %s:%d' % (addr[0], addr[1]))

        while True:
            try:
                payload = client.recv(BUFFER_SIZE)
                if self.controllerMessageHandler(payload.decode()):
                    break
            except:
                break

        client.close()
        log.debug('Close connection with client at %s:%d' % (addr[0], addr[1]))

    def listen(self):
        """
        Act as a TCP server, listen to the instructions from SFC orchestrator
        to create/modify/delete service chains.
        """
        serverSocket = socket(AF_INET, SOCK_STREAM)
        serverSocket.setsockopt(SOL_SOCKET, SO_REUSEADDR, 1)
        serverSocket.bind(('0.0.0.0', NORTHBOUND_PORT))
        serverSocket.listen(100)
        log.info('Listening via Northbound port %d', NORTHBOUND_PORT)

        while True:
            client, addr = serverSocket.accept()
            t = threading.Thread(target=self.clientThread, args=(client, addr))
            t.daemon = True
            t.start()


def launch():
    # core.registerNew(SfcController)

    sfc = core.registerNew(SfcController)

    # conn = sfc.connections.values()[0]
    # conn.setIP('172.31.0.252')
    # node1 = conn.addNode('172.31.0.2', 'fe:fd:02:00:00:01')
    # node2 = conn.addNode('172.31.0.3', 'fe:fd:02:00:00:02')
    # node3 = conn.addNode('172.31.0.4', 'fe:fd:02:00:00:03')
    #
    # chain = ServiceFunctionChain([node2], "1209321-1")
    #
    # conn.addServiceChain(chain)
    # conn.addServicePath(node1, node3, chain.getServiceID())
    #
    # log.info('OK')
