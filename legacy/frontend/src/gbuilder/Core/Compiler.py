"""The compiler that turns topologies into xml network definitions"""

from Core.Item import nodeTypes
from Core.Device import Device
from Core.globals import options, environ, mainWidgets
from PyQt4 import QtCore
from Core.utils import ip_utils
import os
import re
import traceback


class CompileError(Exception):
    pass


class Compiler:
    def __init__(self, device_list, filename):
        """
        Create a compile instance with a list of devices and the filename.
        """
        self.warnings = 0
        self.errors = 0

        self.device_list = device_list

        self.base_network_generator = ip_utils.BaseGiniIPv4Network(
            unicode(options["base_network"]),
            check=True
        )
        self.filename = filename.replace(".gsav", ".xml")
        self.output = open(self.filename, "w")
        self.log = mainWidgets["log"]

        self.compile_list = {}
        for nodeType in nodeTypes:
            self.compile_list[nodeType] = []
        for device in device_list:
            if isinstance(device, Device):
                self.compile_list[device.device_type].append(device)
        # TODO: compile open virtual switch separately
        self.compile_list["Switch"] += self.compile_list["OVSwitch"]

    def compile(self):
        """
        Compile the topology into xml.
        """
        try:
            if options["autogen"]:
                self.log.append("Auto-generate IP/MAC Addresses is ON.")
            else:
                self.log.append("Auto-generate IP/MAC Addresses is OFF.")
            if options["autorouting"]:
                self.log.append("Auto-routing is ON.")
            else:
                self.log.append("Auto-routing is OFF.")

            self.output.write("<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n")
            self.output.write("<!DOCTYPE gloader SYSTEM \"" + os.environ["GINI_SHARE"] + "/gloader/gloader.dtd"+"\">\n")
            self.output.write("<gloader>\n\n")

            if options["autogen"]:
                self.autogen_subnet()
                self.autogen_switch()

            self.compile_subnet()
            self.compile_switch()
            self.switch_pass_mask()

            if options["autogen"]:
                self.autogen_router()
                self.autogen_mach()
                self.autogen_cloud()

            self.routing_table_clear()
            if options["autorouting"]:
                self.routing_table_router()
                self.routing_table_entry()
                self.routing_table_mach()
                self.routing_table_cloud()

            self.compile_router()
            self.compile_mach()
            self.compile_cloud()
            self.compile_openflow_controller()

            self.output.write("</gloader>\n")
            self.output.close()
        except:
            self.errors += 1
            self.log.append("Runtime error when compiling.")
            traceback.print_exc()
        finally:
            self.log.append("Compile finished with " + str(self.errors) +
                            " error(s) and " + str(self.warnings) + " warning(s).\n")
            if self.errors:
                os.remove(self.filename)
                return ""

            return self.filename

    def write_property(self, prop, value):
        """
        Write a property and value in xml format to file.
        """
        self.output.write("\t\t<" + prop + ">")
        self.output.write(value)
        self.output.write("</" + prop + ">\n")

    def generate_node_error(self, device, prop, errorType, interface=None):
        """
        Generate a compile error.
        """
        self.errors += 1
        message = "Error: " + device.getName() + "'s " + prop
        if not errorType == "missing":
            message += " value"
        if interface:
            message += " of interface " + str(device.getInterfaces().index(interface) + 1)
        message += " is " + errorType + "."
        self.log.append('\n' + message + '\n')

    def generate_connection_error(self, device, numCons):
        """
        Generate a compile error.
        """
        self.errors += 1
        message = '\n' + ' '.join(("Error:", device.getName(), "has less than", str(numCons), "connection(s).")) + '\n'
        self.log.append(message)

    def generate_connection_warning(self, device, numCons):
        """
        Generate a compile warning.
        """
        self.warnings += 1
        message = '\n' + ' '.join(("Warning:", device.getName(), "has less than", str(numCons), "connection(s).")) + '\n'
        self.log.append(message)

    def generate_generic_error(self, device, message):
        """
        Generate a generic compile error
        """
        self.errors += 1
        message = '\n' + ' '.join(("Error:", device.getName(), message)) + '\n'
        self.log.append(message)

    def generate_generic_warning(self, device, message):
        """
        Generate a compile warning.
        """
        self.warnings += 1
        message = '\n' + ' '.join(("Warning:", device.getName(), message)) + '\n'
        self.log.append(message)

    def autogen_subnet(self):
        """
        Auto-generate properties for Subnets.
        """
        for subnet in self.compile_list["Subnet"]:
            network = self.base_network_generator.get_next_available_subnet()
            network = ip_utils.GiniIPv4Subnet(network)
            subnet.setup_subnet(network)

    def compile_subnet(self):
        """
        Compile all the Subnets.
        """
        for subnet in self.compile_list["Subnet"]:

            edges = subnet.edges()
            if len(edges) < 1:
                self.generate_connection_warning(subnet, 1)

            isConnectedToRouter = False
            for edge in edges:
                if edge.dest.device_type == "Router" or edge.source.device_type == "Router":
                    isConnectedToRouter = True
            if not isConnectedToRouter and self.compile_list["Router"]:
                self.generate_generic_warning(subnet, " must be connected to at least 1 router.")

            for prop in ["subnet", "mask"]:
                value = subnet.getProperty(prop)
                if not value:
                    self.generate_node_error(subnet, prop, "empty")
                elif not self.validate(prop, value):
                    self.generate_node_error(subnet, prop, "invalid")

            self.pass_mask(subnet)

    def autogen_router(self):
        """
        Auto-generate properties for Routers.
        """
        for router in self.compile_list["Router"]:
            i = 0
            for con in router.edges():
                i += 1
                other_device = con.getOtherDevice(router)
                node = other_device.getTarget(router)

                if options["autogen"]:
                    subnet = str(router.getInterfaceProperty("subnet", node)).rsplit(".", 1)[0]
                    router.setInterfaceProperty("ipv4", "%s.%d" % (subnet, 127 + router.getID()), node)
                    router.setInterfaceProperty("mac", "fe:fd:03:%02x:00:%02x" % (router.getID(), i), node)

    def _check_wireshark_access(self, router):
        if not os.access("/usr/bin/dumpcap", os.X_OK):
            self.generate_generic_warning(
                router,
                "Wireshark may fail to capture traffic.\n"
                "Are you sure you have enough permission to run dumpcap?\n"
                "Check README.md for more information."
            )

    def compile_router(self):
        """
        Compile all the Routers.
        """
        if not self.compile_list["Router"]:
            return
        else:
            first_router = self.compile_list["Router"][0]
            self._check_wireshark_access(first_router)

        for router in self.compile_list["Router"]:
            self.output.write("<vr name=\"" + router.getName() + "\">\n")

            edges = router.edges()
            if len(edges) < 2:
                self.generate_connection_warning(router, 2)

            for con in edges:
                node = con.getOtherDevice(router)
                node = node.getTarget(router)

                self.output.write("\t<netif>\n")

                interface = router.getInterface(node)
                mapping = {"subnet": "network", "mac": "nic", "ipv4": "ip"}
                self.write_interface(router, interface, mapping)

                self.output.write("\t</netif>\n")

            self.output.write("</vr>\n\n")

    def write_interface(self, device, interface, mapping):
        """
        Write an interface to file according to mapping.
        """
        if QtCore.QString("target") in interface:
            self.write_property("target", interface[QtCore.QString("target")].getName())

        for prop, eq in mapping.iteritems():
            try:
                value = interface[QtCore.QString(prop)]
            except:
                self.generate_node_error(device, prop, "missing", interface)

            if not value:
                self.generate_node_error(device, prop, "empty", interface)
            elif not self.validate(prop, value, interface):
                self.generate_node_error(device, prop, "invalid", interface)
            else:
                self.write_property(eq, value)

        table = self.formatRoutes(interface[QtCore.QString("routing")], device.device_type)
        self.output.write(table)

    def autogen_switch(self):
        """
        Auto-generate properties for switches.
        """
        for switch in self.compile_list["Switch"]:
            switch.setProperty("mac", "fe:fd:04:00:00:%02x" % switch.getID())

    def compile_switch(self):
        """
        Compile all the Switches.
        """
        for switch in self.compile_list["Switch"]:
            self.output.write("<vs name=\"" + switch.getName() + "\">\n")

            self.output.write("\t<priority>" + switch.getProperty("Priority") + "</priority>\n")
            self.output.write("\t<mac>" + switch.getProperty("mac") + "</mac>\n")

            subnet = None
            if len(switch.edges()) < 2:
                self.generate_connection_warning(switch, 2)

            first = True
            Q = [switch]
            switch_seen = set([switch])
            while Q:
                t = Q.pop(0)
                for edge in t.edges():
                    node = edge.getOtherDevice(t)
                    if node.device_type == "Subnet":
                        if (subnet is None) or (subnet == node):
                            subnet = node
                        else:
                            self.generate_node_error(t, "subnet", "inconsistent due to multiple values (only connect to a single subnet)")
                            return
                    if node.device_type in ["Switch", "OVSwitch"]:
                        # should look around for a subnet
                        if node not in switch_seen:
                            switch_seen.add(node)
                            Q.append(node)
                            if first:
                                self.output.write("\t<target>" + node.getName() + "</target>\n")
                first = False

            if subnet is None:
                self.generate_node_error(switch, "subnet", "missing")
                return

            switch.setProperty("subnet", subnet.getProperty("subnet"))
            if switch.getProperty("Hub mode") == "True":
                self.output.write("\t<hub/>\n")
            if switch.getProperty("OVS mode") == "True":
                self.output.write("\t<ovs/>\n")
            self.output.write("</vs>\n\n")
            # self.pass_mask(switch)

    def switch_pass_mask(self):
        has_subnet = False
        for switch in self.compile_list["Switch"]:
            for edge in switch.edges():
                node = edge.getOtherDevice(switch)
                if node.device_type == "Subnet":
                    has_subnet = True

            if has_subnet:
                target = switch.getTarget(None)
                gateway = target.getInterface(switch) if target is not None else None
                Q = [switch]
                switch_seen = set([switch])
                while Q:
                    t = Q.pop(0)
                    t.gateway = gateway
                    self.pass_mask(t)
                    for edge in t.edges():
                        node = edge.getOtherDevice(t)
                        if node.device_type in ["Switch", "OVSwitch"]:
                            # should look around for a subnet
                            if node not in switch_seen:
                                switch_seen.add(node)
                                Q.append(node)

    def autogen_mach(self):
        """
        Auto-generate properties for Machs.
        """
        for mach in self.compile_list["Mach"]:
            for interface in mach.getInterfaces():
                try:
                    subnet = str(mach.getInterfaceProperty("subnet")).rsplit(".", 1)[0]
                except:
                    continue
                mach.setInterfaceProperty("ipv4", "%s.%d" % (subnet, mach.getID()+1))
                mach.setInterfaceProperty("mac", "fe:fd:02:00:00:%02x" % mach.getID())

    def compile_mach(self):
        """
        Compile all the Machs.
        """
        for mach in self.compile_list["Mach"]:
            self.output.write("<vm name=\"" + mach.getName() + "\">\n")
            self.output.write("<os>" + mach.getProperty('os') + "</os>")
            interfaces = mach.getInterfaces()
            if len(interfaces) < 1:
                self.generate_connection_warning(mach, 1)

            for interface in interfaces:

                self.output.write("\t<if>\n")

                mapping = {"subnet": "network", "mac": "mac", "ipv4": "ip"}
                self.write_interface(mach, interface, mapping)

                self.output.write("\t</if>\n")

            self.output.write("</vm>\n\n")

    def autogen_cloud(self):
        for cloud in self.compile_list["Cloud"]:
            for interface in cloud.getInterfaces():
                try:
                    subnet = str(cloud.getInterfaceProperty("subnet")).rsplit(".", 1)[0]
                except:
                    continue
                cloud.setInterfaceProperty("ipv4", "%s.%d" % (subnet, 255-cloud.getID()))

    def compile_cloud(self):
        """Compile the cloud component in a topology"""
        for cloud in self.compile_list["Cloud"]:
            self.output.write('<cloud name="%s">\n' % cloud.getName())

            interfaces = cloud.getInterfaces()
            if len(interfaces) < 1:
                self.generate_connection_warning(cloud, 1)

            for interface in interfaces:
                self.output.write("\t<if>\n")

                mapping = {"subnet": "network", "ipv4": "ip"}
                self.write_interface(cloud, interface, mapping)

                self.output.write("\t</if>\n")

            self.output.write('</cloud>\n\n')

    def _check_netns_access(self, openflow_controller):
        """
        Check user's permission to access /var/run/netns. Return true if the
        directory exists and current user has write permission to it, false
        otherwise
        """
        if not os.path.exists("/var/run/netns"):
            self.generate_generic_error(
                openflow_controller,
                "Directory /var/run/netns does not exist.\n\
                Please create it with 'sudo mkdir -p /var/run/netns'\n\
                and change ownership to your user with 'sudo chown -R $USER:$USER /var/run/netns'"
            )
            return False
        else:
            if not os.access("/var/run/netns", os.W_OK):
                self.generate_generic_error(
                    openflow_controller,
                    "You don't have write permission to /var/run/netns."
                )
                return False
        return True

    def compile_openflow_controller(self):
        """
        Compile all the OpenFlow controllers.
        """
        if not self.compile_list["OpenFlowController"]:
            return
        else:
            first_ofc = self.compile_list["OpenFlowController"][0]
            if not self._check_netns_access(first_ofc):
                return

        for controller in self.compile_list["OpenFlowController"]:
            self.output.write("<vofc name=\"" + controller.getName() + "\">\n")

            ovs_found = False
            for connection in controller.edges():
                node = connection.getOtherDevice(controller)
                if node.getProperty("OVS mode") == "True":
                    self.output.write("\t<ovs>" + node.getName() + "</ovs>\n")
                    ovs_found = True
                else:
                    self.generate_generic_warning(controller, "has non-OVS connection; ignored.")

            if not ovs_found:
                self.generate_generic_warning(controller, "has no OVS connections.")

            self.output.write("</vofc>\n\n")

    def pass_mask(self, node):
        """
        Pass the mask between connected devices.
        """
        try:
            subnet = node.getProperty("subnet")
        except:
            self.generate_node_error(node, "subnet", "missing")
            return

        try:
            mask = node.getProperty("mask")
        except:
            self.generate_node_error(node, "mask", "missing")
            return

        for con in node.edges():
            otherDevice = con.getOtherDevice(node)
            if otherDevice.device_type in ["Router", "Mach", "Cloud"]:
                target = node
                if node.device_type == "Subnet":
                    target = node.getTarget(otherDevice)
                    if target is None:
                        continue
                otherDevice.setInterfaceProperty("subnet", subnet, target)
                otherDevice.setInterfaceProperty("mask", mask, target)
            else:
                otherDevice.setProperty("subnet", subnet)
                otherDevice.setProperty("mask", mask)

    def routing_table_clear(self):
        """
        Clear all route tables of interfaceable devices.
        """
        for interfaceable in self.compile_list["Router"]:
            interfaceable.emptyAdjacentLists()
            interfaceable.emptyRouteTable()

        for interfaceable in self.compile_list["Mach"]:
            interfaceable.emptyAdjacentLists()
            interfaceable.emptyRouteTable()

        for interfaceable in self.compile_list["Cloud"]:
            interfaceable.emptyAdjacentLists()
            interfaceable.emptyRouteTable()

    def routing_table_interfaceable(self, devType):
        """
        Compute route tables of devices of type devType.
        """
        for interfaceable in self.compile_list[devType]:
            interfaceable.emptyAdjacentLists()
            interfaceable.emptyRouteTable()
            self.findAdjacentRouters(interfaceable)
            self.findAdjacentSubnets(interfaceable)

    def routing_table_mach(self):
        """
        Compute route tables of Machs.
        """
        self.routing_table_interfaceable("Mach")
        # TODO: Since we're just adding a summarized route to Mach devices,
        # there is no need to compute all the routes. Just finding the default
        # gateway is sufficient, the same goes for Cloud instance
        for mach in self.compile_list["Mach"]:
            for subnet in self.compile_list["Subnet"]:
                if not mach.hasSubnet(subnet.getProperty("subnet")):
                    mach.addRoutingEntry(subnet.getProperty("subnet"))

    def routing_table_cloud(self):
        """
        Compute route tables for Cloud load balancer. To be updated
        """

        # This method is simply copy pasting what we have for Mach devices right now
        self.routing_table_interfaceable("Cloud")

        for cloud in self.compile_list["Cloud"]:
            for subnet in self.compile_list["Subnet"]:
                if not cloud.hasSubnet(subnet.getProperty("subnet")):
                    cloud.addRoutingEntry(subnet.getProperty("subnet"))

    def routing_table_router(self):
        """
        Compute route tables of Routers.
        """
        self.routing_table_interfaceable("Router")

    def routing_table_entry(self):
        """
        Add routing entries for Routers.
        """
        for router in self.compile_list["Router"]:
            for subnet in self.compile_list["Subnet"]:
                router.addRoutingEntry(subnet.getProperty("subnet"))

    def findAdjacentRouters(self, device):
        """
        Find all Routers adjacent to device.
        """
        for interface in device.getInterfaces():
            for con in device.edges():
                otherDevice = con.getOtherDevice(device)
                if otherDevice.device_type == "Subnet":
                    otherDevice = otherDevice.getTarget(device)
                if interface[QtCore.QString("target")] == otherDevice:
                    break

            visitedNodes = []
            self.visitAdjacentRouters(device, con, device, interface, visitedNodes)

    def visitAdjacentRouters(self, myself, con, device, interface, visitedNodes):
        """
        Helper method to find adjacent Routers.
        """
        otherDevice = con.getOtherDevice(device)
        if otherDevice in visitedNodes:
            return

        visitedNodes.append(otherDevice)

        if otherDevice.device_type == "Router":
            myself.addAdjacentRouter(otherDevice, interface)
        elif otherDevice.device_type in ["Mach", "Cloud"]:
            pass
        else:
            for c in otherDevice.edges():
                if con != c:
                    self.visitAdjacentRouters(myself, c, otherDevice, interface, visitedNodes)

    def findSwitchInterface(self, interfaces, switch):
        for i in interfaces:
            if i[QtCore.QString("target")] == switch:
                return i
        return None

    def findAdjacentSubnets(self, device):
        """
        Find all Subnets adjacent to device.
        """
        interface_a = device.getInterfaces()

        for con in device.edges():
            otherDevice = con.getOtherDevice(device)
            if otherDevice.device_type == "Subnet":
                device.addAdjacentSubnet(otherDevice.getProperty("subnet"))
            elif otherDevice.device_type in ["Switch", "OVSwitch"]:
                for c in otherDevice.edges():
                    other_device = c.getOtherDevice(otherDevice)
                    if other_device != device and other_device.device_type == "Router":
                        interface = self.findSwitchInterface(interface_a, otherDevice)
                        device.addAdjacentRouter(other_device, interface)

                device.addAdjacentSubnet(otherDevice.getProperty("subnet"))

    def formatRoutes(self, routes, device_type):
        """
        Format the routes in xml.
        """
        if device_type in ["Mach", "Cloud"] and routes:
            # TODO
            # A quick fix to generalize the routes in Gini. This code should be updated later
            res = ""
            address, mask = str(self.base_network_generator).split("/")
            res += '\t\t<route type="net" '
            res += 'netmask="%s" ' % mask
            res += 'gw="%s">' % routes[0].get(QtCore.QString("gw"))
            res += address
            res += '</route>\n'
            return res
        else:
            header = "\t\t<rtentry "
            gateway = "\" nexthop=\""
            footer = "</rtentry>\n"

        # Because of gloader's get_virtual_machine_interface_outline, we must preserve a specific order of the routes
        outstring = ""
        outstring2 = ""
        for route in routes:
            string = ""
            string += header
            string += "netmask=\"" + route[QtCore.QString("netmask")]
            string += gateway
            string += route[QtCore.QString("gw")]
            string += "\">" + route[QtCore.QString("subnet")]
            string += footer

            if route[QtCore.QString("gw")]:
                outstring2 += string
            else:
                outstring += string

        return outstring + outstring2

    def validate(self, prop, value, interface=None):
        """
        Validate a property of a device or interface.
        """
        if prop == "mac":
            return self.is_valid_mac_address(value)
        elif prop == "ipv4":
            return self.is_valid_ip_subnet(value,
                                           interface[QtCore.QString("subnet")],
                                           interface[QtCore.QString("mask")])
        elif prop == "mask":
            return self.is_valid_mask(value)
        else:
            return self.is_valid_ip_address(value)

    def is_valid_ip_address(self, ip):
        """
        Validate an ip-like address (includes mask).
        """
        return ip_utils.is_valid_ip(ip)

    def is_valid_mask(self, mask):
        """
        Validate a subnet mask.
        """
        if not self.is_valid_ip_address(mask):
            return False

        # Make sure the chunks match the possible values
        octets = [int(octet) for octet in mask.split(".")]

        # The last octet cannot be 255
        if octets[-1] == 255:
            return False
        for octet in octets:
            if octet not in [0, 128, 192, 224, 240, 248, 252, 254, 255]:
                return False

        return True

    def is_valid_ip_subnet(self, ip, subnet, mask):
        """
        Validate an ipv4 address of a host based on the subnet and mask.
        """
        return ip_utils.is_valid_host_in_subnet(ip, subnet, mask)

    def is_valid_mac_address(self, mac):
        """
        Validate a mac address.
        """
        mac = str(mac)
        if re.match(r'^[a-f|0-9]{2}:[a-f|0-9]{2}:[a-f|0-9]{2}:[a-f|0-9]{2}:[a-f|0-9]{2}:[a-f|0-9]{2}$', mac) is None:
            return False
        else:
            return True
