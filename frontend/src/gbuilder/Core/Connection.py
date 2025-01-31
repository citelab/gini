"""The logical connection object that links two devices together"""

from Devices.Bridge import Bridge
from Devices.Firewall import Firewall
from Devices.Hub import Hub
from Devices.Router import Router
from Devices.Subnet import Subnet
from Devices.Switch import Switch
from Devices.Mach import Mach
from UI.Edge import Edge
from Devices.OpenFlowController import OpenFlowController
from Devices.OpenVirtualSwitch import OpenVirtualSwitch
from Devices.Cloud import Cloud

# The connection rules for building topologies
connection_rule = dict()

connection_rule[Mach.device_type] = [
    Switch.device_type,
    Subnet.device_type,
    Bridge.device_type,
    Hub.device_type,
    OpenVirtualSwitch.device_type
]
connection_rule[Router.device_type] = [
    Subnet.device_type,
    Switch.device_type,
    OpenVirtualSwitch.device_type
]
connection_rule[Switch.device_type] = [
    Mach.device_type,
    Subnet.device_type,
    Switch.device_type,
    Router.device_type,
    OpenVirtualSwitch.device_type,
    Cloud.device_type,
]
connection_rule[OpenVirtualSwitch.device_type] = [
    Mach.device_type,
    Subnet.device_type,
    Switch.device_type,
    Router.device_type,
    OpenFlowController.device_type,
    OpenVirtualSwitch.device_type,
    Cloud.device_type
]
connection_rule[Bridge.device_type] = [
    Mach.device_type,
    Subnet.device_type,
    Cloud.device_type
]
connection_rule[Hub.device_type] = [
    Mach.device_type,
    Subnet.device_type,
    Cloud.device_type
]
connection_rule[Subnet.device_type] = [
    Mach.device_type,
    Switch.device_type,
    Router.device_type,
    Bridge.device_type,
    Hub.device_type,
    Firewall.device_type,
    OpenVirtualSwitch.device_type,
    Cloud.device_type
]
connection_rule[Firewall.device_type] = [Subnet.device_type]
connection_rule[OpenFlowController.device_type] = [
    OpenVirtualSwitch.device_type,
]
connection_rule[Cloud.device_type] = [
    Switch.device_type,
    Subnet.device_type,
    Bridge.device_type,
    Hub.device_type,
    OpenVirtualSwitch.device_type
]


class Connection(Edge):
    device_type = "Connection"

    def __init__(self, source, dest):
        """
        Create a connection to link devices together.
        """
        super(Connection, self).__init__(source, dest)

    def getOtherDevice(self, node):
        """
        Retrieve the device opposite to node from this connection.
        """
        if self.source == node:
            return self.dest
        return self.source
