""" Various global variables """
import os

PROG_NAME = "gBuilder"
PROG_VERSION = "5.0.0"

GINI_ROOT = os.environ["GINI_ROOT"]
GINI_HOME = os.environ["GINI_HOME"]

environ = {
    "os": "Windows",
    "path": GINI_ROOT+"/",
    "remotepath": "./",
    "images": os.environ["GINI_SHARE"] + "/gbuilder/images/",
    "config": GINI_HOME+"/etc/",
    "sav": GINI_HOME+"/sav/",
    "tmp": GINI_HOME+"/tmp/",
    "doc": GINI_ROOT+"/doc/"
}

options = {
    "names": True,
    "systray": False,
    "elasticMode": False,
    "keepElasticMode": False,
    "smoothing": True,
    "glowingLights": True,
    "style": "Mac",
    "grid": True,
    "gridColor": "(220,220,220)",
    "background": environ["images"] + "background.jpg",
    "windowTheme": environ["images"] + "window.jpg",
    "baseTheme": environ["images"] + "base.jpg",
    "autorouting": True,
    "autogen": True,
    "autocompile": True,
    "graphing": True,
    "username": "root",
    "server": "localhost",
    "session": "GINI",
    "autoconnect": True,
    "localPort": "10001",
    "remotePort": "10000",
    "restore": True,
    "moveAlert": True,
    "gnome": False,
    "menumods": False,
    "base_network": "172.31.0.0/16"
}

mainWidgets = {
    "app": None,
    "main": None,
    "canvas": None,
    "tab": None,
    "popup": None,
    "inputDialog": None,
    "log": None,
    "tm": None,
    "properties": None,
    "interfaces": None,
    "routes": None,
    "drop": None,
    "client": None,
}

defaultOptions = {"palette": None}

# The list of device types and their current index numbers
hostTypes = {"Mach": 0, "Cloud": 0}
netTypes = {"Switch": 0, "Subnet": 0, "Router": 0, "OpenFlowController": 0, "OVSwitch": 0}
customTypes = {"Custom": 0}
nodeTypes = {
    "Mach": hostTypes, 
    "Switch": netTypes, 
    "Subnet": netTypes,
    "Router": netTypes, 
    "OVSwitch": netTypes, 
    "Custom": customTypes,
    "OpenFlowController": netTypes, 
    "Cloud": hostTypes
}

commonTypes = ["Mach", "Subnet", "Switch", "Router"]
unimplementedTypes = ["Mach_FreeDOS", "Mach_Android", "Firewall"]
