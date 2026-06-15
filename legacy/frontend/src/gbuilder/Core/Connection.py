"""The logical connection object that links two devices together"""

from Devices.Bridge import *
from Devices.Firewall import *
from Devices.Hub import *
from Devices.Router import *
from Devices.Subnet import *
from Devices.Switch import *
from Devices.Mach import *
from UI.Edge import *
from Devices.OpenFlowController import *
from Devices.OpenVirtualSwitch import *
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
