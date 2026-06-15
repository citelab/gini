from Core.Device import *
from Core.Interfaceable import Interfaceable


class Subnet(Device):
    device_type = "Subnet"

    def __init__(self):
        super(Subnet, self).__init__()

        self.num_interface = 0
        self.ip_subnet = None
        self.setProperty("subnet", "")
        self.setProperty("mask", "")

    def setup_subnet(self, subnet):
        self.ip_subnet = subnet
        net, mask = str(subnet).split("/")
        self.setProperty("subnet", net)
        self.setProperty("mask", mask)

    def addEdge(self, edge):
        Device.addEdge(self, edge)

        if len(self.edgeList) == 2:
            node1 = self.edgeList[0].getOtherDevice(self)
            node2 = self.edgeList[1].getOtherDevice(self)
            if isinstance(node1, Interfaceable):
                node1.addInterface(node2)
            if isinstance(node2, Interfaceable):
                node2.addInterface(node1)

    def removeEdge(self, edge):
        if len(self.edgeList) == 2:
            node1 = self.edgeList[0].getOtherDevice(self)
            node2 = self.edgeList[1].getOtherDevice(self)
            if isinstance(node1, Interfaceable):
                node1.removeInterface(node2)
            if isinstance(node2, Interfaceable):
                node2.removeInterface(node1)

        Device.removeEdge(self, edge)

    def getTarget(self, node):
        """Return the other node to which the subnet is connected, if any"""
        for con in self.edges():
            other = con.getOtherDevice(self)
            if other != node:
                return other

    def setProperty(self, prop, value):
        Device.setProperty(self, prop, value)
        if prop == "subnet":
            self.setToolTip(self.getName() + "\nSubnet: " + value)
