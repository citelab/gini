"""The main window for gbuilder"""

import os, time, math, subprocess, sys
from PyQt5 import QtCore, QtGui, QtWidgets
from PyQt5.QtWidgets import QApplication
from UI.DropBar import DropBar
from UI.TabWidget import TabWidget
from UI.LogWindow import LogWindow
from UI.TaskManagerWindow import TaskManagerWindow
from UI.Configuration import ConfigDialog
from UI.Properties import PropertiesWindow
from UI.Tutorial import Tutorial
from UI.StatsWindow import StatsWindow
from UI.Systray import Systray
from Core.globals import options, environ, mainWidgets
from Core.Item import nodeTypes
from UI.ExportWindow import ExportWindow
from Network.gclient import Client
from Core.Compiler import *
from .SendDirectoryWindow import *
from .Properties import *
from .Systray import *
from Network.gclient import *
from Core.Compiler import *
from .TabWidget import *
from .Tutorial import *
from .TaskManagerWindow import *
import Core.globals


class MainWindow(Systray):
    def __init__(self, app):
        """
        Create a main window for the application
        """
        defaultOptions["palette"] = app.palette()
        super(MainWindow, self).__init__()

        self.expansions = 0
        self.client = None
        self.server = None
        self.running = False
        self.recovery = False
        self.filename = ""
        mainWidgets["main"] = self
        mainWidgets["app"] = app

        self.canvas = Canvas(self)
        mainWidgets["canvas"] = self.canvas

        self.tabWidget = TabWidget(self)
        mainWidgets["tab"] = self.tabWidget

        self.setCentralWidget(self.tabWidget)

        self.createActions()
        self.createMenus()
        self.createToolBars()
        self.createStatusBar()
        self.createDockWindows()
        self.createConfigWindows()
        self.createPopupWindows()
        self.createProgressBar()

        self.newScene()

        self.debugWindow.hide()
        self.tm.hide()
        self.routes.hide()

        self.setVisible(True)
        self.center()
        self.saveLayout(environ["config"] + "defaultLayout")

        if options["menumods"]:
            self.setStyleSheet("""QToolTip {
                               background-color: black;
                               color: white;
                               border: black solid 1px
                               }
                               QMenu {
                               color: white;
                               }""")
        else:
            self.setStyleSheet("""QToolTip {
                               background-color: black;
                               color: white;
                               border: black solid 1px
                               }""")

        self.defaultLayout = True
        if options["restore"]:
            self.loadLayout()
            self.defaultLayout = False

        self.loadProject()

    def center(self):
        """
        Center the window.
        """
        screen = QtWidgets.QDesktopWidget().screenGeometry()
        rect = self.geometry()
        
        # Convert float division results to integers
        x = int((screen.width() - rect.width()) / 2)
        y = int((screen.height() - rect.height()) / 2)
        
        self.move(x, y)
        self.show()

    def getProject(self):
        """
        Return the project.
        """
        return self.project

    def startTutorial(self):
        """
        Start the interactive tutorial.
        """
        if isinstance(mainWidgets["canvas"], Tutorial):
            self.log.append("You are already doing the tutorial!  If you would like to stop or restart, select 'Close' from the File menu now.")
            return

        if not self.closeTopology():
            return

        self.project = "Tutorial"
        self.filename = ""

        self.canvas = Tutorial(self)
        mainWidgets["canvas"] = self.canvas

        self.tabWidget.removeTab(0)
        self.tabWidget.addTab(self.canvas, "Tutorial")
        self.canvas.start()

        for nodeType in nodeTypes.keys():
            itemTypes = nodeTypes[nodeType]
            itemTypes[nodeType] = 0

        self.properties.clear()
        self.interfaces.clear()
        self.routes.clear()

        self.resetLayout(True)
        self.lockDocks()

    def lockDocks(self):
        """
        Lock the dock windows so they cannot be moved, closed or resized.
        """
        self.tm.hide()
        for dock in self.docks.values():
            dock.setFeatures(dock.NoDockWidgetFeatures)

    def unlockDocks(self):
        """
        Unlock the dock windows.
        """
        self.tm.show()
        for dock in self.docks.values():
            dock.setFeatures(dock.DockWidgetClosable | dock.DockWidgetMovable | dock.DockWidgetFloatable)

    @staticmethod
    def faq():
        """
        Open the FAQ in the default browser.
        """
        old_dir = os.getcwd()
        os.chdir(environ["doc"])
        load_path = os.getcwd()
        os.chdir(old_dir)

        url = QtCore.QUrl("file://" + load_path + "/FAQ.html")
        QtCore.QDesktopServices.openUrl(url)

    def closeTopology(self):
        """
        Close the current topology.
        """
        if self.running:
            self.log.append("You cannot close a topology when one is still running!")
            return False

        scene = self.canvas.scene()
        if scene and scene.items():
            reply = QtWidgets.QMessageBox.warning(
                self,
                self.tr(Core.globals.PROG_NAME),
                self.tr("Save before closing?"),
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No | QtWidgets.QMessageBox.Cancel
            )
            if reply == QtWidgets.QMessageBox.Yes:
                if not self.saveTopology():
                    return False
            elif reply == QtWidgets.QMessageBox.No:
                pass
            else:
                return False

        if isinstance(mainWidgets["canvas"], Tutorial):
            self.canvas = Canvas(self)
            mainWidgets["canvas"] = self.canvas
            self.tabWidget.removeTab(0)
            self.tabWidget.addTab(self.canvas, "Default Project")
            self.project = ""
            self.unlockDocks()

        self.filename = ""
        scene = Scene(self.canvas)
        scene.setItemIndexMethod(QtWidgets.QGraphicsScene.NoIndex)
        self.canvas.setScene(scene)
        self.expansions = 0

        for nodeType in nodeTypes.keys():
            itemTypes = nodeTypes[nodeType]
            itemTypes[nodeType] = 0

        self.properties.clear()
        self.interfaces.clear()
        self.routes.clear()

        return True

    def sendFile(self):
        """
        Start a process to select and send a file to the server.
        """
        if not self.server or self.server.poll() is not None:
            self.log.append("Please start the server first!")
            return
        if not self.client or not self.client.isConnected():
            self.startClient()

        filename = self.loadFile("All Files (*.*)")
        if not filename:
            return

        self.sendWindow.setFilename(filename)
        self.sendWindow.show()

    def newScene(self):
        """
        Close the current topology and create a new one.
        """
        if self.running:
            self.log.append("You cannot create a new topology when one is still running!")
            return

        if isinstance(mainWidgets["canvas"], Tutorial):
            self.log.append("You cannot create a new topology during the tutorial!")
            return

        if not self.closeTopology():
            return

        self.expandScene()

    def expandScene(self):
        """
        Expand the scene.
        """
        scene = self.canvas.scene()
        if not scene:
            return
            
        x = scene.width()
        y = scene.height()
        
        item = QtWidgets.QGraphicsLineItem(-x, -y, x, y)
        scene.addItem(item)
        scene.removeItem(item)
        
        self.expansions += 1

    def newProject(self):
        """
        Create a new project for device sharing.
        """
        if self.running:
            self.log.append("You cannot create a new project when one is still running!")
            return

        if isinstance(mainWidgets["canvas"], Tutorial):
            self.log.append("You cannot create a new project during the tutorial!")
            return

        filename = self.saveFile("gproj")
        if filename.isEmpty():
            return

        project_name = str(filename).split("/")[-1].strip(".gproj")
        from Core.Item import nodeTypes
        for nodeType in nodeTypes:
            if project_name.startswith(nodeType + "_"):
                self.popup.setWindowTitle("Invalid Project Name")
                self.popup.setText("You cannot name a project starting with the name of a device and underscore!")
                self.popup.show()
                return

        self.project = str(filename)
        file = QtCore.QFile(filename)
        if not file.open(QtCore.QFile.WriteOnly | QtCore.QFile.Text):
            QtWidgets.QMessageBox.warning(self, self.tr("Save Error"),
                                      self.tr("Cannot write file %1:\n%2.")
                                      .arg(self.filename)
                                      .arg(file.errorString()))
            return

        out = QtCore.QTextStream(file)
        QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)

        if options["username"]:
            out << "username=" + options["username"] + "\n"
        else:
            self.log.append("Warning, no username is specified!")

        if options["session"]:
            out << "session=" + options["session"] + "\n"
        elif options["server"]:
            out << "server=" + options["server"] + "\n"
        else:
            self.log.append("Warning, no server or session name is specified!")

        QApplication.restoreOverrideCursor()

        self.tabWidget.addTab(self.canvas, project_name)

    def openProject(self):
        """
        Load an existing project for device sharing.
        """
        if self.isRunning():
            self.log.append("You cannot open a project when one is still running!")
            return

        if isinstance(mainWidgets["canvas"], Tutorial):
            self.log.append("You cannot open a project during the tutorial!")
            return

        filename = self.loadFile("GPROJ (*.gproj)")
        if filename.isEmpty():
            return

        self.project = str(filename)
        self.loadProject()

    def loadProject(self):
        """
        Load project file data into options.
        """
        if not self.project:
            self.tabWidget.addTab(self.canvas, "Default Project")
            return

        file = QtCore.QFile(self.project)
        if not file.open(QtCore.QFile.ReadOnly | QtCore.QFile.Text):
            QtWidgets.QMessageBox.warning(self, self.tr("Load Error"),
                                      self.tr("Cannot read file %1:\n%2.")
                                      .arg(self.project)
                                      .arg(file.errorString()))
            self.tabWidget.addTab(self.canvas, "Default Project")
            return

        _in = QtCore.QTextStream(file)
        QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)

        while not _in.atEnd():
            line = str(_in.readLine())
            option, value = line.split("=", 1)
            options[option] = value

        self.configWindow.updateSettings()

        QApplication.restoreOverrideCursor()
        project_name = self.project.split("/")[-1].strip(".gproj")
        self.tabWidget.addTab(self.canvas, project_name)

    def closeProject(self):
        """
        Close the current project.
        """
        if self.isRunning():
            self.log.append("You cannot close a project when it is still running!")
            return

        if isinstance(mainWidgets["canvas"], Tutorial):
            self.log.append("You cannot close a project during the tutorial!")
            return

        if self.tabWidget.count() == 1:
            self.tabWidget.addTab(self.canvas, "Default Project")
            self.project = ""
        else:
            self.tabWidget.removeTab(0)

    def export(self):
        """
        Open an export window to generate an image from the canvas.
        """
        self.exportWindow.show()

    def startBackend(self):
        """
        Start the backend server. Wait for 2000ms after startServer() and start
        the Gini client, that way gBuilder is attached to the gServer terminal.
        """
        self.startServer()
        QtCore.QTimer.singleShot(2000, self.startClient)

    def setRecovery(self, recovery):
        """
        Set the recovering state of the topology.
        """
        self.recovery = recovery

    def isRunning(self):
        """
        Returns whether a topology is running or not.
        """
        return self.running

    def startServer(self):
        """
        Start the server backend of gbuilder, which controls running topologies.
        """
        if self.server and self.server.poll() is None:
            self.log.append("A server is already running!")
            return

        base = "ssh -t %s@%s" % (options["username"], options["server"])
        tunnel = " -L %s:localhost:%s" % (options["localPort"], options["remotePort"])
        server = "bash -c -i 'gserver %s' || sleep 5" % options["remotePort"]
        command = ""
        gserver = "gserver"

        command += "xterm -fa 'Monospace' -fs 14 -title \"" + gserver + "\" -e " + base + tunnel + " \" " + server + "\""

        self.server = subprocess.Popen(str(command), shell=True, preexec_fn=os.setpgrp)

    def startClient(self):
        """
        Start the client of gbuilder, which communicates with the server.
        """
        self.client = Client(self)
        self.client.connectTo("localhost", int(options["localPort"]), 10)
        mainWidgets["client"] = self.client

    def compile(self):
        """
        Compile the current topology.
        """
        if self.running:
            self.log.append("You cannot compile a topology when one is still running!")
            return False

        if not self.saveTopology():
            return False

        scene = self.canvas.scene()
        compiler = Compiler(scene.items(), self.filename)
        xmlFile = compiler.compile()

        self.properties.display()
        self.interfaces.display()
        self.routes.display()

        if xmlFile:
            self.statusBar().showMessage(self.tr("Compiled '%1'").arg(xmlFile), 2000)
            return True
        else:
            self.statusBar().showMessage(self.tr("Compile failed"), 2000)
            return False

    def run(self):
        """
        Run the current topology.
        """
        if not self.server or self.server.poll() is not None:
            self.log.append("Please start the server first!")
            return
        if not self.client or not self.client.isConnected():
            self.startClient()

        if self.isRunning() and not self.recovery:
            self.log.append("A topology is already running, please stop it first!")
            return

        scene = self.canvas.scene()
        items = scene.items()
        if items:
            if self.recovery:
                self.recovery = False
            elif options["autocompile"] and not self.compile():
                return
        else:
            self.log.append("Please create or load a topology first!")
            return

        options["elasticMode"] = False

        xmlFile = self.filename.replace(".gsav", ".xml")

        if not os.access(xmlFile, os.F_OK):
            self.log.append("Please compile the topology first!")
            return

        self.tm.show()

        self.client.process("file . " + xmlFile)
        self.client.send("init " + self.project.split("/")[-1].strip(".gproj"))
        self.client.send("canvas %d,%d" % (scene.width(), scene.height()))
        self.client.process("start " + xmlFile)

        self.running = True
        self.canvas.setAcceptDrops(False)
        scene = self.canvas.scene()
        scene.startRefresh()
        scene.clearSelection()

        self.properties.clear()
        self.interfaces.clear()
        self.routes.clear()

        self.statusBar().showMessage(self.tr("Network is up and running!"), 2000)

    def stop(self):
        """
        Stop the current running topology.
        """
        if not self.server or self.server.poll() is not None:
            self.log.append("Please start the server first!")
            return
        if not self.isRunning():
            self.log.append("No network topology is running!")
            return
        if not self.client or not self.client.isConnected():
            self.startClient()

        if self.recovery:
            self.recovery = False

        scene = self.canvas.scene()
        activeDevices = False
        from Core.Device import Device
        for item in scene.items():
            if not isinstance(item, Device):
                continue
            if item.device_type == "Router":
                item.stop()
            if item.status:
                activeDevices = True

        if not activeDevices:
            self.stopped()
        elif not scene.isRefreshing():
            scene.startRefresh()

        self.client.process("stop")

    def stopped(self):
        """
        Handle a fully stopped topology.
        """
        self.running = False
        self.canvas.scene().stopRefresh()
        self.tm.hide()
        self.canvas.setAcceptDrops(True)

        old_dir = os.getcwd()
        os.chdir(environ["tmp"])
        for tmp_file in os.listdir("."):
            if tmp_file.startswith("."):
                continue
            try:
                os.remove(tmp_file)
            except:
                continue
        os.chdir(old_dir)

        self.statusBar().showMessage(self.tr("Stopped!"), 2000)

    def loadFile(self, filetype):
        """
        Load a file through a file dialog.
        """
        # Qt is very picky in the filename structure but python is not, so we use python
        # to form the correct path which will work for both Windows and Linux
        old_dir = os.getcwd()
        os.chdir(environ["sav"])
        load_path = os.getcwd()
        os.chdir(old_dir)

        filename = QtWidgets.QFileDialog.getOpenFileName(
            self,
            self.tr("Choose a file name"), load_path,
            self.tr(filetype))

        return filename

    def loadRealTopologyFile(self, filetype):
        """
        Load a real topology name
        """
        self.popup.setWindowTitle("Topology Names")
        self.popup.setText("You are about to select from the list:\n1.Ernet")
        self.popup.setStandardButtons(QtWidgets.QMessageBox.Ok | QtWidgets.QMessageBox.Cancel)
        self.popup.show()
        retval = self.popup.exec_()

        if retval == 1024:
            old_dir = os.getcwd()
            os.chdir(environ["sav"])
            os.chdir("exist")
            load_path = os.getcwd()
            os.chdir(old_dir)

            filename = QtWidgets.QFileDialog.getOpenFileName(
                self,
                self.tr("Choose a file name"), load_path,
                self.tr(filetype))

            return filename

    def loadRealTopology(self):
        """
        Load a real topology.
        """
        if self.isRunning():
            self.log.append("You cannot load a topology when one is still running!")
            return

        if isinstance(mainWidgets["canvas"], Tutorial):
            self.log.append("You cannot load a topology during the tutorial!")
            return

        def loadIntoScene(line, *args):
            scene = self.canvas.scene()
            itemType, arg = line.split(":")
            args = str(arg).strip("()").split(",")

            if itemType == "edge":
                source = scene.findItem(args[0])
                dest = scene.findItem(args[1])
                item = Connection(source, dest)
                scene.addItem(item)
            else:
                devType, index = str(itemType).rsplit("_", 1)
                item = deviceTypes[devType]()
                item.setIndex(int(index))
                scene.addItem(item)
                item.setPos(float(args[0]), float(args[1]))
                item.nudge()

            return item

        def loadProperties(itemDict):
            currentInterfaceTarget = None
            currentRouteSubnet = None

            for item, properties in itemDict.iteritems():
                for line in properties:
                    count = 0
                    while line.find("\t") == 0:
                        line = line[1:]
                        count += 1

                    prop, value = line.split(":", 1)
                    if count == 1:
                        item.setProperty(prop, value)
                    elif count == 2:
                        currentInterfaceTarget = self.canvas.scene().findItem(value)
                    elif count == 3:
                        item.setInterfaceProperty(prop, value, currentInterfaceTarget)
                    elif count == 4:
                        currentRouteSubnet = value
                        item.addEntry("", "", value, currentInterfaceTarget)
                    elif count == 5:
                        item.setEntryProperty(prop, value, currentRouteSubnet, currentInterfaceTarget)

        filename = self.loadRealTopologyFile("GSAV (*.gsav)")
        if not filename:
            return

        file = QtCore.QFile(filename)
        if not file.open(QtCore.QFile.ReadOnly | QtCore.QFile.Text):
            QtWidgets.QMessageBox.warning(
                self,
                self.tr("Load Error"),
                self.tr("Cannot read file %1:\n%2.")
                .arg(filename)
                .arg(file.errorString()))
            return

        self.newScene()
        self.filename = str(filename)

        _in = QtCore.QTextStream(file)

        QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)

        itemDict = {}
        _in.seek(0)
        line = str(_in.readLine())
        lines = []

        while not _in.atEnd():
            item = loadIntoScene(line)
            line = str(_in.readLine())
            while line.find("\t") == 0:
                lines.append(line)
                line = str(_in.readLine())
            itemDict[item] = lines
            lines = []

        loadProperties(itemDict)

        QApplication.restoreOverrideCursor()

        self.statusBar().showMessage(self.tr("Loaded '%1'").arg(filename), 2000)

    def loadTopology(self):
        """
        Load a topology.
        """
        if self.isRunning():
            self.log.append("You cannot load a topology when one is still running!")
            return

        if isinstance(mainWidgets["canvas"], Tutorial):
            self.log.append("You cannot load a topology during the tutorial!")
            return

        def loadIntoScene(line, *args):
            scene = self.canvas.scene()
            itemType, arg = line.split(":")
            args = str(arg).strip("()").split(",")

            if itemType == "edge":
                source = scene.findItem(args[0])
                dest = scene.findItem(args[1])
                item = Connection(source, dest)
                scene.addItem(item)
            else:
                devType, index = str(itemType).rsplit("_", 1)
                item = deviceTypes[devType]()
                item.setIndex(int(index))
                scene.addItem(item)
                item.setPos(float(args[0]), float(args[1]))
                item.nudge()

            return item

        def loadProperties(itemDict):
            currentInterfaceTarget = None
            currentRouteSubnet = None

            for item, properties in itemDict.iteritems():
                for line in properties:
                    count = 0
                    while line.find("\t") == 0:
                        line = line[1:]
                        count += 1

                    prop, value = line.split(":", 1)
                    if count == 1:
                        item.setProperty(prop, value)
                    elif count == 2:
                        currentInterfaceTarget = self.canvas.scene().findItem(value)
                    elif count == 3:
                        item.setInterfaceProperty(prop, value, currentInterfaceTarget)
                    elif count == 4:
                        currentRouteSubnet = value
                        item.addEntry("", "", value, currentInterfaceTarget)
                    elif count == 5:
                        item.setEntryProperty(prop, value, currentRouteSubnet, currentInterfaceTarget)

        filename = self.loadFile("GSAV (*.gsav)")
        if filename.isEmpty():
            return

        file = QtCore.QFile(filename)
        if not file.open(QtCore.QFile.ReadOnly | QtCore.QFile.Text):
            QtWidgets.QMessageBox.warning(
                self,
                self.tr("Load Error"),
                self.tr("Cannot read file %1:\n%2.")
                .arg(filename)
                .arg(file.errorString()))
            return

        self.newScene()
        self.filename = str(filename)

        _in = QtCore.QTextStream(file)

        QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)

        itemDict = {}
        _in.seek(0)
        line = str(_in.readLine())
        lines = []

        while not _in.atEnd():
            item = loadIntoScene(line)
            line = str(_in.readLine())
            while line.find("\t") == 0:
                lines.append(line)
                line = str(_in.readLine())
            itemDict[item] = lines
            lines = []

        loadProperties(itemDict)

        QApplication.restoreOverrideCursor()

        self.statusBar().showMessage(self.tr("Loaded '%1'").arg(filename), 2000)

    def saveFile(self, filetype):
        """
        Save a file through a file dialog.
        """
        old_dir = os.getcwd()
        os.chdir(environ["sav"])
        save_path = os.getcwd()
        os.chdir(old_dir)

        filename = QtWidgets.QFileDialog.getSaveFileName(
            self,
            self.tr("Choose a file name"), save_path,
            self.tr(filetype.upper() + " (*.%s)" % filetype))

        if filename.isEmpty():
            return filename

        if not filename.toLower().endsWith("." + filetype):
            filename += "." + filetype

        return filename

    def saveTopologyAs(self):
        """
        Save the topology under a new name.
        """
        if not self.canvas.scene().items():
            return

        filename = self.saveFile("GINI Topology (*.gtop)")
        if filename:
            if not filename.endswith(".gtop"):
                filename += ".gtop"
            self.filename = filename
            self.saveTopology()

    def saveTopology(self):
        """
        Save a topology.
        """
        scene = self.canvas.scene()

        if not scene.items():
            self.log.append("There is nothing to save!")
            return False

        # for first time use
        if not self.filename:
            return self.saveTopologyAs()

        file = QtCore.QFile(self.filename)
        if not file.open(QtCore.QFile.WriteOnly | QtCore.QFile.Text):
            QtWidgets.QMessageBox.warning(
                self,
                self.tr("Save Error"),
                self.tr("Cannot write file %1:\n%2.")
                .arg(self.filename)
                .arg(file.errorString()))
            return False

        out = QtCore.QTextStream(file)
        QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        outstring = ""
        for item in scene.items():
            if isinstance(item, Node):
                outstring += item.toString()

        for item in scene.items():
            if isinstance(item, Edge):
                outstring += item.toString()

        out << outstring
        QApplication.restoreOverrideCursor()

        self.statusBar().showMessage(self.tr("Saved '%1'").arg(self.filename), 2000)

        return True

    def copy(self):
        """
        Copy the selected items to the clipboard.
        """
        scene = self.canvas.scene()
        selectedItems = scene.selectedItems()
        if not selectedItems:
            return

        # Implement copy functionality here
        self.log.append("Copy functionality not implemented yet")

    def config(self):
        """
        Open the options window.
        """
        self.configWindow.show()

    def get_ip_address(ifname):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        return socket.inet_ntoa(fcntl.ioctl(
            s.fileno(),
            0x8915,  # SIOCGIFADDR
            struct.pack('256s', ifname[:15])
        )[20:24])

    def arrange(self):
        """
        Rearrange the topology based on the distance between nodes.
        """
        if self.isRunning():
            self.log.append("Cannot arrange while running!")
            return

        if isinstance(mainWidgets["canvas"], Tutorial):
            mainWidgets["log"].append("Cannot arrange during the tutorial!")
            return

        options["elasticMode"] = not options["elasticMode"]

    def about(self):
        """
        Show the about window.
        """
        QtWidgets.QMessageBox.about(self,
                                self.tr("About %s %s"
                                        % (Core.globals.PROG_NAME,
                                            Core.globals.PROG_VERSION)),
                                self.tr("<b>%s %s</b><br>Written by Daniel Ng<br>under the supervision of Muthucumaru Maheswaran"
                                        % (Core.globals.PROG_NAME,
                                            Core.globals.PROG_VERSION)))

    def createActions(self):
        """
        Create the actions for the menus and toolbars.
        """
        self.newSceneAct = QtWidgets.QAction(QtGui.QIcon(environ["images"] + "new.png"), self.tr("&New"), self)
        self.newSceneAct.setShortcut(self.tr("Ctrl+N"))
        self.newSceneAct.setStatusTip(self.tr("Create a new topology"))
        self.newSceneAct.triggered.connect(self.newScene)

        self.loadAct = QtWidgets.QAction(QtGui.QIcon(environ["images"] + "open.png"), self.tr("&Open..."), self)
        self.loadAct.setShortcut(self.tr("Ctrl+O"))
        self.loadAct.setStatusTip(self.tr("Open an existing topology"))
        self.loadAct.triggered.connect(self.loadTopology)

        self.saveAct = QtWidgets.QAction(QtGui.QIcon(environ["images"] + "save.png"), self.tr("&Save"), self)
        self.saveAct.setShortcut(self.tr("Ctrl+S"))
        self.saveAct.setStatusTip(self.tr("Save the topology"))
        self.saveAct.triggered.connect(self.saveTopology)

        self.saveAsAct = QtWidgets.QAction(self.tr("Save &As..."), self)
        self.saveAsAct.setStatusTip(self.tr("Save the topology under a new name"))
        self.saveAsAct.triggered.connect(self.saveTopologyAs)

        self.copyAct = QtWidgets.QAction(QtGui.QIcon(environ["images"] + "copy.png"), self.tr("&Copy"), self)
        self.copyAct.setShortcut(self.tr("Ctrl+C"))
        self.copyAct.setStatusTip(self.tr("Copy the selected items"))
        self.copyAct.triggered.connect(self.copy)

        self.exportAct = QtWidgets.QAction(QtGui.QIcon(environ["images"] + "export.png"), self.tr("&Export"), self)
        self.exportAct.setStatusTip(self.tr("Export topology to file"))
        self.exportAct.triggered.connect(self.export)

        self.closeAct = QtWidgets.QAction(QtGui.QIcon(environ["images"] + "close.png"), self.tr("&Close"), self)
        self.closeAct.setShortcut(self.tr("Ctrl+W"))
        self.closeAct.setStatusTip(self.tr("Close the topology"))
        self.closeAct.triggered.connect(self.closeTopology)

        self.quitAct = QtWidgets.QAction(self.tr("&Quit"), self)
        self.quitAct.setShortcut(self.tr("Ctrl+Q"))
        self.quitAct.setStatusTip(self.tr("Quit the application"))
        self.quitAct.triggered.connect(self.quit)

        self.sendFileAct = QtWidgets.QAction(QtGui.QIcon(environ["images"] + "send.png"), self.tr("&Send File..."), self)
        self.sendFileAct.setShortcut(self.tr("Ctrl+F"))
        self.sendFileAct.setStatusTip(self.tr("Choose a file to send to the server"))
        self.sendFileAct.triggered.connect(self.sendFile)

        self.compileAct = QtWidgets.QAction(QtGui.QIcon(environ["images"] + "compile.png"), self.tr("&Compile"), self)
        self.compileAct.setShortcut(self.tr("Ctrl+E"))
        self.compileAct.setStatusTip(self.tr("Compile the current topology"))
        self.compileAct.triggered.connect(self.compile)

        self.runAct = QtWidgets.QAction(QtGui.QIcon(environ["images"] + "run.png"), self.tr("&Run"), self)
        self.runAct.setShortcut(self.tr("Ctrl+R"))
        self.runAct.setStatusTip(self.tr("Run the current topology"))
        self.runAct.triggered.connect(self.run)

        self.stopAct = QtWidgets.QAction(QtGui.QIcon(environ["images"] + "stop.png"), self.tr("&Stop"), self)
        self.stopAct.setShortcut(self.tr("Ctrl+D"))
        self.stopAct.setStatusTip(self.tr("Stop the current topology"))
        self.stopAct.triggered.connect(self.stop)

        self.startServerAct = QtWidgets.QAction(QtGui.QIcon(environ["images"] + "startServer.png"), self.tr("&Start Server"), self)
        self.startServerAct.setShortcut(self.tr("Ctrl+T"))
        self.startServerAct.setStatusTip(self.tr("Start the server"))
        self.startServerAct.triggered.connect(self.startBackend)

        self.optionsAct = QtWidgets.QAction(QtGui.QIcon(environ["images"] + "options.png"), self.tr("&Options"), self)
        self.optionsAct.setShortcut(self.tr("F2"))
        self.optionsAct.setStatusTip(self.tr("Show the options window"))
        self.optionsAct.triggered.connect(self.config)

        self.arrangeAct = QtWidgets.QAction(QtGui.QIcon(environ["images"] + "arrange.png"), self.tr("&Arrange"), self)
        self.arrangeAct.setShortcut(self.tr("Ctrl+A"))
        self.arrangeAct.setStatusTip(self.tr("Arrange the items"))
        self.arrangeAct.triggered.connect(self.arrange)

        self.resetLayoutAct = QtWidgets.QAction(QtGui.QIcon(environ["images"] + "layout.png"), self.tr("Reset Layout"), self)
        self.resetLayoutAct.setStatusTip(self.tr("Reset dock windows to the saved layout"))
        self.resetLayoutAct.triggered.connect(self.resetLayout)

        self.expandSceneAct = QtWidgets.QAction(QtGui.QIcon(environ["images"] + "expand.png"), self.tr("Expand Scene"), self)
        self.expandSceneAct.setStatusTip(self.tr("Expand the scene for more space"))
        self.expandSceneAct.triggered.connect(self.expandScene)

        self.newProjectAct = QtWidgets.QAction(QtGui.QIcon(environ["images"] + "new.png"), self.tr("&New"), self)
        self.newProjectAct.setShortcut(self.tr("Ctrl+Shift+N"))
        self.newProjectAct.setStatusTip(self.tr("Create a new project"))
        self.newProjectAct.triggered.connect(self.newProject)

        self.openProjectAct = QtWidgets.QAction(QtGui.QIcon(environ["images"] + "open.png"), self.tr("&Open"), self)
        self.openProjectAct.setShortcut(self.tr("Ctrl+Shift+O"))
        self.openProjectAct.setStatusTip(self.tr("Open an existing project"))
        self.openProjectAct.triggered.connect(self.openProject)

        self.closeProjectAct = QtWidgets.QAction(QtGui.QIcon(environ["images"] + "close.png"), self.tr("&Close"), self)
        self.closeProjectAct.setShortcut(self.tr("Ctrl+Shift+W"))
        self.closeProjectAct.setStatusTip(self.tr("Close the current project"))
        self.closeProjectAct.triggered.connect(self.closeProject)

        self.tutorialAct = QtWidgets.QAction(QtGui.QIcon(environ["images"] + "tutorial.png"), self.tr("&Tutorial"), self)
        self.tutorialAct.triggered.connect(self.startTutorial)

        self.faqAct = QtWidgets.QAction(QtGui.QIcon(environ["images"] + "help.png"), self.tr("&FAQ"), self)
        self.faqAct.triggered.connect(self.faq)

        self.aboutAct = QtWidgets.QAction(QtGui.QIcon(environ["images"] + "giniLogo.png"), self.tr("&About"), self)
        self.aboutAct.setStatusTip(self.tr("Show the application's About box"))
        self.aboutAct.triggered.connect(self.about)

        self.aboutQtAct = QtWidgets.QAction(QtGui.QIcon(environ["images"] + "Qt-logo.png"), self.tr("About &Qt"), self)
        self.aboutQtAct.setStatusTip(self.tr("Show the Qt library's About box"))
        self.aboutQtAct.triggered.connect(QtWidgets.qApp.aboutQt)

    def createMenus(self):
        """
        Create the menus with actions.
        """
        self.fileMenu = self.menuBar().addMenu(self.tr("&File"))
        self.fileMenu.setPalette(defaultOptions["palette"])
        self.fileMenu.addAction(self.newSceneAct)
        self.fileMenu.addAction(self.loadAct)
        self.fileMenu.addAction(self.saveAct)
        self.fileMenu.addAction(self.saveAsAct)
        self.fileMenu.addAction(self.sendFileAct)
        self.fileMenu.addAction(self.exportAct)
        self.fileMenu.addAction(self.closeAct)
        self.fileMenu.addSeparator()
        self.fileMenu.addAction(self.quitAct)

        self.projectMenu = self.menuBar().addMenu(self.tr("&Project"))
        self.projectMenu.setPalette(defaultOptions["palette"])
        self.projectMenu.addAction(self.newProjectAct)
        self.projectMenu.addAction(self.openProjectAct)
        self.projectMenu.addAction(self.closeProjectAct)

        self.editMenu = self.menuBar().addMenu(self.tr("&Edit"))
        self.editMenu.setPalette(defaultOptions["palette"])
        self.editMenu.addAction(self.copyAct)
        self.editMenu.addAction(self.arrangeAct)
        self.editMenu.addAction(self.resetLayoutAct)
        self.editMenu.addAction(self.expandSceneAct)

        self.runMenu = self.menuBar().addMenu(self.tr("&Run"))
        self.runMenu.setPalette(defaultOptions["palette"])
        self.runMenu.addAction(self.compileAct)
        self.runMenu.addAction(self.runAct)
        self.runMenu.addAction(self.stopAct)
        self.runMenu.addAction(self.startServerAct)

        self.configMenu = self.menuBar().addMenu(self.tr("&Config"))
        self.configMenu.setPalette(defaultOptions["palette"])
        self.configMenu.addAction(self.optionsAct)

        self.menuBar().addSeparator()

        self.helpMenu = self.menuBar().addMenu(self.tr("&Help"))
        self.helpMenu.setPalette(defaultOptions["palette"])
        self.helpMenu.addAction(self.tutorialAct)
        self.helpMenu.addAction(self.faqAct)
        self.helpMenu.addAction(self.aboutAct)
        self.helpMenu.addAction(self.aboutQtAct)

    def createPopupMenu(self):
        """
        Customize the popup menu so that it is visible.
        """
        popupMenu = QtWidgets.QMainWindow.createPopupMenu(self)
        popupMenu.setPalette(defaultOptions["palette"])
        return popupMenu

    def createToolBars(self):
        """
        Create the toolbars with actions.
        """
        self.fileToolBar = self.addToolBar(self.tr("File"))
        self.fileToolBar.addAction(self.newSceneAct)
        self.fileToolBar.addAction(self.loadAct)
        self.fileToolBar.addAction(self.saveAct)
        self.fileToolBar.addAction(self.sendFileAct)
        self.fileToolBar.addAction(self.exportAct)
        self.fileToolBar.addAction(self.closeAct)

        self.editToolBar = self.addToolBar(self.tr("Edit"))
        self.editToolBar.addAction(self.copyAct)
        self.editToolBar.addAction(self.resetLayoutAct)
        self.editToolBar.addAction(self.expandSceneAct)

        self.runToolBar = self.addToolBar(self.tr("Run"))
        self.runToolBar.addAction(self.startServerAct)
        self.runToolBar.addAction(self.compileAct)
        self.runToolBar.addAction(self.runAct)
        self.runToolBar.addAction(self.stopAct)

    def createStatusBar(self):
        """
        Create the status bar.
        """
        self.statusBar().showMessage(self.tr("Ready"))

    def createProgressBar(self):
        """
        Create the progress bar.
        """
        self.progressBar = QtWidgets.QProgressBar()
        self.progressBar.setRange(0, 10000)
        self.progressBar.setValue(0)

        self.statusBar().addPermanentWidget(self.progressBar)
        self.progressBar.show()

    def getDeviceCount(self, alive=False):
        """
        Return the interfaceable device count, or the alive ones if alive=True.
        """
        from Core.Interfaceable import Interfaceable

        count = 0.0
        for item in self.canvas.scene().items():
            if isinstance(item, Interfaceable):
                if alive and item.status in ("", "dead"):
                    continue
                count += 1.0

        return count

    def updateProgressBar(self):
        """
        Update the progress bar.
        """
        maxVal = self.progressBar.maximum()
        finalVal = (self.getDeviceCount(True) / self.getDeviceCount()) * maxVal

        if finalVal < 0:
            finalVal = 0

        self.progressBar.setValue(finalVal)

        if finalVal == 0:
            return True

        return False

    def createConfigWindows(self):
        """
        Create the options window.
        """
        self.configWindow = ConfigDialog(self)

    def createDockWindows(self):
        """
        Create the dock windows: dropbar, log, properties, interfaces, routes.
        """
        self.log = LogWindow(self.tr("Log"), self)
        self.log.append("Welcome to %s %s!\n"
                % (Core.globals.PROG_NAME, Core.globals.PROG_VERSION))
        self.log.append("To open an existing topology, please click the 'Open' icon from the tray above canvas!")
        self.log.setGeometry(QtCore.QRect(0, 0, 800, 114))
        mainWidgets["log"] = self.log

        self.dropbar = DropBar(self.tr("Components"), self)
        self.dropbar.setGeometry(QtCore.QRect(0, 0, 129, 390))
        mainWidgets["drop"] = self.dropbar

        self.properties = PropertiesWindow(self)
        self.properties.setWindowTitle("Properties")
        mainWidgets["properties"] = self.properties

        self.interfaces = InterfacesWindow(self)
        self.interfaces.setWindowTitle("Interfaces")
        mainWidgets["interfaces"] = self.interfaces

        self.routes = RoutesWindow(self.interfaces, self)
        self.routes.setWindowTitle("Routes")
        mainWidgets["routes"] = self.routes

        self.tm = TaskManagerWindow(self)
        self.tm.setWindowTitle("Task Manager")
        mainWidgets["tm"] = self.tm

        self.debugWindow = QtWidgets.QDockWidget(self.tr("Debug Window"))
        self.debugWindow.setWidget(DebugWindow(self))

        self.docks = {
            "Components": self.dropbar,
            "Log": self.log,
            "Properties": self.properties,
            "Interfaces": self.interfaces,
            "Routes": self.routes,
            "Task Manager": self.tm
        }

        self.addDockWidget(QtCore.Qt.LeftDockWidgetArea, self.dropbar)
        self.addDockWidget(QtCore.Qt.BottomDockWidgetArea, self.log)
        self.addDockWidget(QtCore.Qt.RightDockWidgetArea, self.properties)
        self.addDockWidget(QtCore.Qt.RightDockWidgetArea, self.interfaces)
        self.addDockWidget(QtCore.Qt.RightDockWidgetArea, self.routes)
        self.addDockWidget(QtCore.Qt.RightDockWidgetArea, self.tm)

        self.tm.setFloating(True)
        self.routes.setFloating(True)
        self.debugWindow.setFloating(True)

    def createPopupWindows(self):
        """
        Create the different popup windows.
        """
        self.exportWindow = ExportWindow(self)
        self.sendWindow = SendDirectoryWindow(self)
        self.popup = QtWidgets.QMessageBox(self)
        self.popup.setIcon(QtWidgets.QMessageBox.Warning)
        self.popup.setWindowIcon(QtGui.QIcon(environ["images"]+"giniLogo.png"))
        mainWidgets["popup"] = self.popup
        # specific dialog for client IP and port input
        self.inputDialog = QtWidgets.QInputDialog(self)
        self.inputDialog.setWindowIcon(QtGui.QIcon(environ["images"]+"giniLogo.png"))
        mainWidgets["dialog"] = self.inputDialog

    def keyPressEvent(self, event):
        """
        Handle specific shortcut keys.
        """
        key = event.key()
        scene = self.canvas.scene()
        if key == QtCore.Qt.Key_Escape:
            scene.clearSelection()
        elif key == QtCore.Qt.Key_Delete:
            for item in scene.selectedItems():
                item.delete()
        elif key == QtCore.Qt.Key_C:
            items = scene.items()
            if not items:
                return
            selected = scene.selectedItems()
            scene.clearSelection()
            if selected:
                index = items.index(selected[0])
                items[index - 1].setSelected(True)
            else:
                items[0].setSelected(True)
        elif key == QtCore.Qt.Key_H:
            for dock in self.docks.values():
                dock.setFloating(not dock.isFloating())
        elif key == QtCore.Qt.Key_F10:
            self.debugWindow.show()
        else:
            super(MainWindow, self).keyPressEvent(event)

    def stop_server(self):
        if self.server and self.server.poll() is None:
            self.server.terminate()

    def quit(self):
        return super(MainWindow, self).quit()

    def closeEvent(self, event):
        """
        Handle close event
        """
        if super(MainWindow, self).closeEvent(event):
            self.stop_server()
            event.accept()
        else:
            event.ignore()


class DebugWindow(QtWidgets.QWidget):
    def __init__(self, parent):
        super(DebugWindow, self).__init__()

        self.parent = parent
        self.layout = QtWidgets.QVBoxLayout()
        self.button = QtWidgets.QPushButton("Execute")
        self.line_edit = QtWidgets.QLineEdit()
        self.layout.addWidget(self.line_edit)
        self.layout.addWidget(self.button)
        self.setLayout(self.layout)

        self.windows = {}
        for key, val in mainWidgets.items():
            if key != "app" and key != "client" and val is not None:
                self.windows[key] = val

        self.button.clicked.connect(self.execute)

    @staticmethod
    def fill(self):
        scene = mainWidgets["canvas"].scene()
        for i in range(125):
            scene.addItem(Mach())

    def execute(self):
        canvas = mainWidgets["canvas"]
        canvas.scene()

        text = str(self.line_edit.text())
        if text:
            lines = text.split(";")
            for line in lines:
                print(eval(line))

        if isinstance(canvas, Tutorial):
            canvas.next()
