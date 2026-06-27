#!/usr/bin/python2

# file: program.py

from optparse import OptionParser
import os

import xml_processor


class Program:
    """Class to define the gloader program structure"""
    progName = ""       # name of the program
    setupFile = ""      # file that contains the setup

    optParser = None    # the parser to parse the cmd line options
    options = None      # the cmd line option storage
    createOpt = False   # to check whether the create option is used
    destroyOpt = False  # to check whether the destroy option is used

    giniNW = None       # the gini network framework

    def __init__(self, pName, fileName):
        """Initialize the Program class"""
        self.progName = pName
        self.setupFile = fileName
        self.optParser = OptionParser(usage=self.usage())

        self.optParser.add_option("-d", "--destroy",
                                  action="callback",
                                  callback=self.destroyCallBack,
                                  dest="xmlFile",
                                  help="Destroy a GINI instance")
        self.optParser.add_option("-c", "--create",
                                  action="callback",
                                  callback=self.createCallBack,
                                  dest="xmlFile",
                                  help="Create/recreate a GINI instance")
        self.optParser.add_option("-s", "--switch-dir",
                                  dest="switchDir",
                                  default=".",
                                  help="Specify the switch configuration directory")
        self.optParser.add_option("-r", "--router-dir",
                                  dest="routerDir",
                                  default=".",
                                  help="Specify the router configuration directory")
        self.optParser.add_option("-u", "--mach-dir",
                                  dest="machDir",
                                  default="",
                                  help="Specify the Mach configuration directory")
        self.optParser.add_option("-C", "--cloud-dir",
                                  dest="cloud_dir",
                                  default="",
                                  help="Specify the Cloud configuration directory")
        self.optParser.add_option("-o", "--controller-dir",
                                  dest="controllerDir",
                                  default="",
                                  help="Specify the OpenFlow controller configuration directory")
        self.optParser.add_option("-b", "--bin-dir",
                                  dest="binDir",
                                  default="",
                                  help="Specify the directory of the GINI binaries")
        self.optParser.add_option("-k", "--keep-old",
                                  dest="keepOld",
                                  action="store_true",
                                  default=False,
                                  help="Specify not to destroy existing GINI instances")

    def usage(self):
        """creates the usage message"""
        usageString = self.progName
        usageString += " (-d [gloader-xml-file] | -c [gloader-xml-file])"
        usageString += " [-s switch-dir]"
        usageString += " [-r router-dir]"
        usageString += " [-u mach-dir]"
        usageString += " [-b bin-dir]"
        usageString += " [-C cloud-dir]"
        usageString += " [-k]"
        return usageString

    def createCallBack(self, option, opt_str, value, parser):
        """Handling the optional argument for the option -c"""
        value = ""
        rargs = parser.rargs
        if len(rargs) > 0:
            currArg = rargs[0]
            if currArg[:1] != "-":
                value = currArg
                del rargs[0]
        self.createOpt = True
        setattr(parser.values, option.dest, value)
        
    def destroyCallBack(self, option, opt_str, value, parser):
        """Handling the optional argument for the option -d"""
        value = ""
        rargs = parser.rargs
        if len(rargs) > 0:
            currArg = rargs[0]
            if currArg[:1] != "-":
                value = currArg
                del rargs[0]
        self.destroyOpt = True
        setattr(parser.values, option.dest, value)
        
    def processOptions(self, args):
        """Processing options and checking the provided XML file (if any)"""
        # parse the command line arguments and extract options
        (self.options, args) = self.optParser.parse_args(args)
        # check the extract options and generate necessary
        # error messages
        if self.destroyOpt and self.createOpt:
            print "Use either -d or -c, not both"
            print self.usage()
            return False
        if (not self.destroyOpt) and (not self.createOpt):
            print "At least -d or -c option should be given"
            print self.usage()
            return False
        # if no XML file is given read from the gini setup file
        if not self.options.xmlFile:
            if os.access(self.setupFile, os.R_OK):
                setupFileHandle = open(self.setupFile)
                lines = setupFileHandle.readlines()
                self.options.xmlFile = lines[0].strip()
                self.options.switchDir = lines[1].strip()
                self.options.routerDir = lines[2].strip()
                self.options.machDir = lines[3].strip()
                self.options.cloud_dir = lines[3].strip()
                self.options.binDir = lines[4].strip()
                self.options.controllerDir = lines[5].strip()
                setupFileHandle.close()
            else:
                print "No XML file specified and no setup file in the current directory"
                return False
        # check the validity of the XML file
        if not os.access(self.options.xmlFile, os.R_OK):
            print 'Can not read file "' + self.options.xmlFile + '"'
            return False
        # if everything is fine, start XML processing
        if self.options.xmlFile != "":
            myXMLProcess = xml_processor.XMLProcessor(self.options.xmlFile)
            if not myXMLProcess.checkSemantics():
                return False
            # get the GINI network setup from the XML processor
            self.giniNW = myXMLProcess.giniNW
            return True
