#!/usr/bin/python2

# file: xml_processor.py

import xml.dom.minidom
import os

import utilities
from gini_nw import GINI_NW


class XMLProcessor:
    """Processing XML network specification file"""
    xmlFile = ""
    giniNW = None

    def __init__(self,file):
        """Initiates the class with the XML file and validates it"""
        self.xmlFile = file
        self.validateXML()
        self.getGINI()

    def validateXML(self):
        """Validate the specified xmlFile; need not to be called from outside"""
        pass
        # myValidator = xmlval.XMLValidator()
        # myValidator.parse_resource(self.xmlFile)

    def getGINI(self):
        # get the doc DOM object
        docDOM = xml.dom.minidom.parse(self.xmlFile)
        # create the GINI network
        self.giniNW = GINI_NW(docDOM)

    def checkSemantics(self):
        """
        Checking the semantic correctness of the XML specification.
        Returns the doc DOM object
        """
        result = True
        # Rule 1: No duplicate switch names
        result = result and self.checkDuplicateNames("Switch", self.giniNW.switches)
        # Rule 2: No duplicate router names
        result = result and self.checkDuplicateNames("Router", self.giniNW.vr)
        # Rule 3: No duplicate Mach names
        result = result and self.checkDuplicateNames("Mach", self.giniNW.vm)
        # Rule 4: No duplicate ports in switches
        result = result and self.checkDuplicatePorts(self.giniNW.switches)
        # Rule 5: Given filesystem is correct (not applicable to docker)
        # result = result and self.checkFileSystem(self.giniNW.vm)
        # result = result and self.checkDuplicateNames("REALM", self.giniNW.vrm)
        # Rule 6: No duplication in network interface configuration &&
        # Rule 7: Valid IP addresses in network interface configuration
        for vm in self.giniNW.vm:
            if not self.checkDuplicateVMIF(vm.name, vm.interfaces):
                result = False
        for vr in self.giniNW.vr:
            if not self.checkDuplicateVRIF(vr.name, vr.netIF):
                result = False
        return result

    def checkDuplicateNames(self, elemName, elements):
        """checking for duplicate switch names"""
        result = True
        names = []
        for item in elements:
            currName = item.name
            if utilities.findIndex(names, currName) != -1:
                print elemName + ': Duplicate names: "' + currName + '"'
                result = False
            else:
                names.append(currName)
        return result

    def checkDuplicatePorts(self, elements):
        """checking for duplicate switch names"""
        result = True
        ports = []
        for item in elements:
            currPort = item.port
            if currPort:
                # neglect empty port specification
                if utilities.findIndex(ports, currPort) != -1:
                    print item.name + ": Duplicate ports: \"" + currPort + "\""
                    result = False
                else:
                    ports.append(currPort)
        return result

    def checkFileSystem(self, elements):
        """checks the validity of the file system"""
        result = True
        for vm in elements:
            if vm.fileSystem.name and \
                    not os.access(vm.fileSystem.name, os.R_OK):
                print vm.name + ": " + vm.fileSystem.name + " is not readable"
                result = False
        return result

    def checkDuplicateVMIF(self, vmName, elements):
        return True

    def checkDuplicateVRIF(self, vrName, elements):
        return True

    def checkValidIPv4(self, name, ip):
        return True
