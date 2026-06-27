from Core.Attachable import *
from .Switch import Switch
from PyQt4.QtCore import QPoint


class OpenVirtualSwitch(Switch, Attachable):
    """
    Class definition for Open Virtual Switch devices
    """
    device_type = "OVSwitch"

    def __init__(self):
        super(OpenVirtualSwitch, self).__init__()
        self.setProperty("OVS mode", "True")

        self.lightPoint = QPoint(-10, -5)

    def addEdge(self, edge):
        return Switch.addEdge(self, edge)

    def removeEdge(self, edge):
        return Switch.removeEdge(self, edge)

    def getGateway(self):
        return Switch.getGateway(self)

    def getTarget(self, node):
        return Switch.getTarget(self, node)
