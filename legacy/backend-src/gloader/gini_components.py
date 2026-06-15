# file: gini_components.py

# this file contains the class definitions of
# the GINI components and sub-components
# the printMe() methods can be used for debug purposes
# the attributes for each class matches the DTD specification


class Switch:
    name = ""
    targets = None
    port = ""
    remote = "localhost"
    hub = False
    isOVS = False

    def __init__(self, sName):
        """Initialize the Switch class"""
        self.name = sName
        self.targets = []
        self.priority = ""
        self.mac = ""

    def addTarget(self, newTarget):
        self.targets.append(newTarget)

    def printMe(self):
        print "[Name: " + self.name + "]",
        print "[Port: " + self.port + "]",
        print "[Remote: " + self.remote + "]",
        print "[Hub: " + str(self.hub) + "]"
        print "[IsOVS " + str(self.isOVS) + "]"


class VM:
    name = ""
    os = None
    mem = ""
    kernel = ""
    boot = ""
    interfaces = None

    def __init__(self, vmName):
        """Initialize the Virtual Machine class"""
        self.name = vmName
        self.interfaces = []

    def addInterface(self, newIF):
        self.interfaces.append(newIF)

    def printMe(self):
        print "[Name: " + self.name + "]",
        if self.os:
            print "[os-version: " + self.os + "]",
        if self.mem:
            print "[Mem: " + self.mem + "]",
        if self.kernel:
            print "[Kernel: " + self.kernel + "]",
        print ""
        print "[Interfaces: %d" % len(self.interfaces),
        for item in self.interfaces:
            item.printMe()
        print "end if]"


class VMInterface:
    name = ""
    target = ""
    network = ""
    mac = ""
    ip = ""
    routes = None

    def __init__(self, ifName):
        """Initialize the VMInterface class"""
        self.name = ifName
        self.routes = []

    def addRoute(self, newRoute):
        self.routes.append(newRoute)

    def printMe(self):
        print "[Name: " + self.name + "]",
        print "[Target: " + self.target + "]",
        print "[MAC: " + self.mac + "]",
        print "[IP: " + self.ip + "]"
        print "[Routes: "
        for item in self.routes:
            item.printMe()
        print "end routes]"
        return True


class VMRoute:
    dest = ""
    type = ""
    netmask = ""
    gw = ""

    def __init__(self):
        """Initialize the VMRoute class"""
        pass

    def printMe(self):
        print "[Dest: " + self.dest + "]",
        print "[Type: " + self.type + "]",
        print "[NetMask: " + self.netmask + "]",
        print "[GW: " + self.gw + "]"


class MacLayer:
    macType = ""
    trans = ""

    def __init__(self):
        """Initialize the MacLayer class"""
        pass

    def printMe(self):
        print "[macType: " + self.macType + "]",
        print "[trans: " + self.trans + "]",


class VR:
    name = ""
    netIF = None
    tunIF = None
    cli = False

    def __init__(self, vrName):
        """Initialize the VR class"""
        self.name = vrName
        self.netIF = []
        self.tunIF = []

    def addNetIF(self, newIF):
        self.netIF.append(newIF)

    def addTunIF(self, tunIF):
        self.tunIF.append(tunIF)

    def printMe(self):
        print "[Name: " + self.name + "]",
        print "[CLI: " + str(self.cli) + "]",
        print "[NetIF: "
        for item in self.netIF:
            item.printMe()
        print "]"


class VOFC:
    def __init__(self, vofcName):
        """Initialize the VOFC class"""
        self.name = vofcName
        self.open_virtual_switches = []

    def add_open_virtual_switch(self, new_open_virtual_switch):
        self.open_virtual_switches.append(new_open_virtual_switch)

    def printMe(self):
        print "[Name: " + self.name + "]",
        print ""
        print "[Routers: "
        for item in self.open_virtual_switches:
            print "[Name: " + item + "]"
        print "]"


class VRInterface:
    name = ""
    target = ""
    nic = ""
    ip = ""
    network = ""
    gw = ""
    mtu = ""
    routes = None

    def __init__(self, ifName):
        """Initialize the VRInterface class"""
        self.name = ifName
        self.routes = []

    def addRoute(self, newRoute):
        self.routes.append(newRoute)

    def printMe(self):
        print "[Name: " + self.name + "]",
        print "[target: " + self.target + "]",
        print "[nic: " + self.nic + "]",
        print "[IP: " + self.ip + "]",
        if self.mtu:
            print "[MTU: " + self.mtu + "]",
        print ""
        print "[Routes: "
        for item in self.routes:
            item.printMe()
        print "]"


class VRRoute:
    dest = ""
    netmask = ""
    nexthop = ""

    def __init__(self):
        """Initialize the VRRoute class"""
        pass

    def printMe(self):
        print "[Dest: " + self.dest + "]",
        print "[netmask: " + self.netmask + "]",
        if self.nexthop:
            print "[nexthop: " + self.nexthop + "]",
        print ""


class Cloud:
    def __init__(self, name):
        self.name = name
        self.interfaces = []

    def add_interface(self, interface):
        self.interfaces.append(interface)

    def __str__(self):
        return "Gini Cloud instance: {Name: %s, Interfaces: %s}" % (self.name, self.interfaces)


class CloudInterface:
    def __init__(self):
        self.target = ""
        self.network = ""
        self.ip = ""
        self.mac = ""
        self.routes = []

    def add_route(self, route):
        self.routes.append(route)
