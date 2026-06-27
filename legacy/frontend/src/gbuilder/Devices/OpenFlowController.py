from Core.Connection import *
from Core.globals import environ, mainWidgets, GINI_ROOT, GINI_HOME, defaultOptions
from PyQt4.QtCore import QPoint
from Core.Attachable import *
import os
import re


class OpenFlowController(Attachable):
    device_type = "OpenFlowController"

    def __init__(self):
        super(OpenFlowController, self).__init__()

        # Shortens display name
        cur_name = self.getProperty("name")
        self.setProperty("name", cur_name.replace("OpenFlowController", "OFController"))

        self.modulesFilePath = os.path.join(GINI_HOME, "data", self.getName(), self.getName() + ".modules")

        loadModuleMenu = self.menu.addMenu("Load Module")
        loadModuleMenu.setPalette(defaultOptions["palette"])
        self.modulesPath = os.path.join(GINI_ROOT, "lib/gini/pox/ext/gini")

        self.coreMenu = loadModuleMenu.addMenu("Core")
        self.coreMenu.setPalette(defaultOptions["palette"])
        self.coreMenu.aboutToShow.connect(self.showCoreMenu)
        self.customMenu = loadModuleMenu.addMenu("Custom")
        self.customMenu.setPalette(defaultOptions["palette"])
        self.customMenu.aboutToShow.connect(self.showCustomMenu)
        self.samplesMenu = loadModuleMenu.addMenu("Samples")
        self.samplesMenu.setPalette(defaultOptions["palette"])
        self.samplesMenu.aboutToShow.connect(self.showSamplesMenu)

        self.lightPoint = QPoint(-10, -3)

    def showCoreMenu(self):
        self.showMenu(self.coreMenu, "core")

    def showCustomMenu(self):
        self.showMenu(self.customMenu, "custom")

    def showSamplesMenu(self):
        self.showMenu(self.samplesMenu, "samples")

    def setIndex(self, index):
        super(Attachable, self).setIndex(index)

        # Shortens display name
        cur_name = self.getProperty("name")
        self.setProperty("name", cur_name.replace("OpenFlowController", "OFController"))

        # Updates module file path
        self.modulesFilePath = os.path.join(GINI_HOME, "data", self.getName(), self.getName() + ".modules")

    def showMenu(self, menu, pkgName):
        """
        The following code requires a bit of an explanation.

        We need to dynamically generate right-click menus for the OpenFlow controller
        that contain a list of POX modules the user can launch. When a menu item is
        clicked, the appropriate POX module should be launched.

        Normally, we could accomplish this simply by creating a signal handler function called
        "loadModule" and binding the trigger signal of each menu item to that function. The
        function would be able to use the QObject sender function to identify the module
        required and launch it.

        Unfortunately, for some reason, this doesn't work. We can't add a signal handler to the
        OpenFlowController class, since it doesn't extend QObject and won't have access
        to the sender() function. We can't have this class inherit from QObject, nor can we
        create a separate class that extends QObject, because we keep getting cryptic error
        messages when we try to do this or access sender().

        Since we don't have access to sender(), we're stuck creating unique functions for each
        menu item to call that in turn simply call "loadModule" with a package name. Since
        the menu items are dynamically generated, this approach requires meta-programming,
        which is very ugly, but this seems to be the only way to do it. :(
        """
        menu.clear()
        path = os.path.join(self.modulesPath, pkgName)
        files = [f for f in os.listdir(path) if os.path.isfile(os.path.join(path, f)) and f.endswith(".py")]
        for f in files:
            if f != "__init__.py":
                fullPkgName = "gini." + pkgName + "." + os.path.splitext(f)[0]
                safeFullPkgName = re.sub(r'[^A-Za-z0-9_]', '_', fullPkgName)    # Accepted chars in Python function names

                self.addLoadModuleFunction(fullPkgName, safeFullPkgName)
                menu.addAction(fullPkgName, getattr(self, "loadModule_" + safeFullPkgName))

    def addLoadModuleFunction(self, fullPkgName, safeFullPkgName):
        def loadModuleFunction(instance):
            instance.loadModule(fullPkgName)

        loadModuleFunction.__name__ = "loadModule_" + safeFullPkgName
        setattr(OpenFlowController, loadModuleFunction.__name__, loadModuleFunction)

    def loadModule(self, fullPkgName):
        if not mainWidgets["main"].isRunning():
            mainWidgets["log"].append("You must start the topology first!")
            return

        file_name = open(self.modulesFilePath, "w")
        file_name.write(fullPkgName + "\n")
        file_name.close()
