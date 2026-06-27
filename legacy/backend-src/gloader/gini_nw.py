# file: gini_nw.py

import os
import xml.dom.minidom
from gini_components import *


class GINI_NW:
    def __init__(self, docDOM):
        """Initialize the GINI_NW class"""
        self.switches = []
        self.vm = []
        self.vr = []
        self.vofc = []
        self.clouds = []
        self.getSwitches(docDOM.getElementsByTagName("vs"))
        self.getVMs(docDOM.getElementsByTagName('vm'))
        self.getVRs(docDOM.getElementsByTagName("vr"))
        self.getVOFCs(docDOM.getElementsByTagName("vofc"))
        self.get_clouds(docDOM.getElementsByTagName("cloud"))

    def getSwitches(self, elements):
        """get the switch configuration"""
        for switch in elements:
            newSwitch = Switch(switch.getAttribute("name"))
            for para in switch.childNodes:
                if para.nodeType == para.ELEMENT_NODE:
                    if para.tagName.lower() == "priority":
                        newSwitch.priority = self.getTextPart(para)
                    if para.tagName.lower() == "mac":
                        newSwitch.mac = self.getTextPart(para)
                    if para.tagName.lower() == "target":
                        newSwitch.targets.append(self.getTextPart(para))
                    if para.tagName.lower() == "port":
                        newSwitch.port = self.getTextPart(para)
                    if para.tagName.lower() == "remote":
                        newSwitch.remote = self.getTextPart(para)
                    if para.tagName.lower() == "hub":
                        newSwitch.hub = True
                    if para.tagName.lower() == "ovs":
                        newSwitch.isOVS = True
            self.switches.append(newSwitch)
        return True

    def getVMs(self, elements):
        """get virtual machine configurations"""
        for vm in elements:
            newVM = VM(vm.getAttribute("name"))
            for para in vm.childNodes:
                if para.nodeType == para.ELEMENT_NODE:
                    if para.tagName.lower() == "mem":
                        newVM.mem = self.getTextPart(para)
                    if para.tagName.lower() == "os":
                        newVM.os = self.getTextPart(para)
                    if para.tagName.lower() == "boot":
                        newVM.boot = self.getBoot(para)
                    if para.tagName.lower() == "if":
                        newIF = self.getVMIF(para, len(newVM.interfaces))
                        newVM.addInterface(newIF)
            self.vm.append(newVM)
        return True

    def get_clouds(self, elements):
        """Get Cloud instance configurations"""
        for cloud in elements:
            new_cloud = Cloud(cloud.getAttribute("name"))
            for para in cloud.childNodes:
                if para.nodeType == para.ELEMENT_NODE:
                    if para.tagName.lower() == "if":
                        new_interface = self.get_cloud_interface(para)
                        new_cloud.add_interface(new_interface)
            self.clouds.append(new_cloud)
        return True

    def get_cloud_interface(self, element):
        new_interface = CloudInterface()
        for para in element.childNodes:
            if para.nodeType == para.ELEMENT_NODE:
                tag_name = para.tagName.lower()
                if tag_name == "target":
                    new_interface.target = self.getTextPart(para)
                elif tag_name == "network":
                    new_interface.network = self.getTextPart(para)
                elif tag_name == "ip":
                    new_interface.ip = self.getTextPart(para)
                elif tag_name == "route":
                    new_route = self.getVMRoute(para)
                    new_interface.add_route(new_route)
        return new_interface

    def getVOFCs(self, elements):
        """get OpenFlow controller configurations"""
        for vofc in elements:
            newVOFC = VOFC(vofc.getAttribute("name"))
            for para in vofc.childNodes:
                if para.nodeType == para.ELEMENT_NODE:
                    if para.tagName.lower() == "ovs":
                        newVOFC.add_open_virtual_switch(self.getTextPart(para))
            self.vofc.append(newVOFC)
        return True

    def getVRs(self, elements):
        """Get router specification"""
        for router in elements:
            newVR = VR(router.getAttribute("name"))
            numberOfTun = 0
            for para in router.childNodes:
                if para.nodeType == para.ELEMENT_NODE:
                    if para.tagName.lower() == "cli":
                        newVR.cli = True
                    if para.tagName.lower() == "netif":
                        newIF = self.getVRIF(para, len(newVR.netIF)+1)
                        newVR.addNetIF(newIF)
                    if para.tagName.lower() == "controller":
                        newVR.openFlowController = self.getTextPart(para)
                    if para.tagName.lower() == "loctun":
                        newIF = self.getTUNIF(para, numberOfTun)
                        numberOfTun += 1
                        newVR.addTunIF(newIF)
            self.vr.append(newVR)
        return True

    @staticmethod
    def getTextPart(elem):
        """Extract the text within the element"""
        for textPart in elem.childNodes:
            if textPart.nodeType == textPart.TEXT_NODE:
                remoteName = textPart.nodeValue.strip()
                if remoteName:
                    return remoteName
        return ""

    def getBoot(self, elem):
        """get boot element in VM specification"""
        for part in elem.childNodes:
            if part.nodeType == part.ELEMENT_NODE and \
                    part.tagName.lower() == "con0":
                return self.getTextPart(part)
        return ""

    def getVMIF(self, elem, count):
        """get VM network interface specification"""
        ifName = "eth%d" % count
        myIF = VMInterface(ifName)
        for para in elem.childNodes:
            if para.nodeType == para.ELEMENT_NODE:
                if para.tagName.lower() == "target":
                    myIF.target = self.getTextPart(para)
                if para.tagName.lower() == "network":
                    myIF.network = self.getTextPart(para)
                if para.tagName.lower() == "mac":
                    myIF.mac = self.getTextPart(para)
                if para.tagName.lower() == "ip":
                    myIF.ip = self.getTextPart(para)
                if para.tagName.lower() == "route":
                    newRoute = self.getVMRoute(para)
                    myIF.addRoute(newRoute)
        return myIF

    def getVMRoute(self, elem):
        """Extract VM route entries"""
        newRoute = VMRoute()
        newRoute.type = elem.getAttribute("type")
        newRoute.netmask = elem.getAttribute("netmask")
        newRoute.gw = elem.getAttribute("gw")
        newRoute.dest = self.getTextPart(elem)
        return newRoute

    def getVRIF(self, elem, index):
        """get virtual router network interface"""
        ifName = "eth%d" % index
        myIF = VRInterface(ifName)
        for para in elem.childNodes:
            if para.nodeType == para.ELEMENT_NODE:
                if para.tagName.lower() == "target":
                    myIF.target = self.getTextPart(para)
                if para.tagName.lower() == "nic":
                    myIF.nic = self.getTextPart(para)
                if para.tagName.lower() == "ip":
                    myIF.ip = self.getTextPart(para)
                if para.tagName.lower() == "network":
                    myIF.network = self.getTextPart(para)
                if para.tagName.lower() == "gw":
                    myIF.gw = self.getTextPart(para)
                if para.tagName.lower() == "mtu":
                    myIF.mtu = self.getTextPart(para)
                if para.tagName.lower() == "rtentry":
                    newRoute = self.getVRRoute(para)
                    myIF.addRoute(newRoute)
        return myIF

    def getTUNIF(self, elem, index):
        """get virtual router network interface"""
        ifName = "tun%d" % index
        myIF = VRInterface(ifName)
        for para in elem.childNodes:
            if para.nodeType == para.ELEMENT_NODE:
                if para.tagName.lower() == "target":
                    myIF.target = self.getTextPart(para)
                if para.tagName.lower() == "nic":
                    myIF.nic = self.getTextPart(para)
                if para.tagName.lower() == "ip":
                    myIF.ip = self.getTextPart(para)
                if para.tagName.lower() == "network":
                    myIF.network = self.getTextPart(para)
                if para.tagName.lower() == "gw":
                    myIF.gw = self.getTextPart(para)
                if para.tagName.lower() == "mtu":
                    myIF.mtu = self.getTextPart(para)
                if para.tagName.lower() == "rtentry":
                    newRoute = self.getVRRoute(para)
                    myIF.addRoute(newRoute)
        return myIF

    def getVRRoute(self, elem):
        """Extract VR route entries"""
        newRoute = VRRoute()
        newRoute.netmask = elem.getAttribute("netmask")
        newRoute.nexthop = elem.getAttribute("nexthop")
        newRoute.dest = self.getTextPart(elem)
        return newRoute
