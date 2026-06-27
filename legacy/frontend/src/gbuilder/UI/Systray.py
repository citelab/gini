"""The system tray used by the main window"""

import sys, os
from PyQt4 import QtCore, QtGui
from Core.globals import *
from Tutorial import Tutorial


class Systray(QtGui.QMainWindow):
    def __init__(self, parent=None):
        """
        Create a system tray window to appear in the taskbar.
        """
        super(Systray, self).__init__(parent)

        self.project = ""

        self.createTrayActions()
        self.createTrayIcon()
        self.icon = QtGui.QIcon(environ["images"] + "giniLogo.png")
        self.setIcon(self.icon)

        QtCore.QObject.connect(
            self.trayIcon,
            QtCore.SIGNAL("activated(QSystemTrayIcon::ActivationReason)"),
            self.iconActivated
        )

    def quit(self):
        """
        Quit the program and avoid the system tray settings.
        """
        systray = options["systray"]
        options["systray"] = False
        self.close()
        options["systray"] = systray

    def closeEvent(self, event):
        """
        Handle the close event based on system tray settings.
        """
        if options["systray"]:
            self.hide()
            self.trayIcon.show()
            event.ignore()
            return False

        elif self.canvas.scene().items():
            if not self.closeTopology():
                event.ignore()
                return False

        if options["restore"]:
            self.saveLayout()

        return True

    def resetLayout(self, default=False):
        """
        Toggle the layout between the default and the saved layout.
        """
        if not default and isinstance(mainWidgets["canvas"], Tutorial):
            self.log.append("You cannot reset the layout during the Tutorial!")
            return

        if default:
            self.defaultLayout = True
        else:
            self.defaultLayout = not self.defaultLayout
        if self.defaultLayout:
            self.loadLayout(environ["config"] + "defaultLayout")
        else:
            self.loadLayout()

    def saveLayout(self, filename=""):
        """
        Save the layout.
        """
        def getGeometry(window):
            geo = window.geometry()
            return "(%d,%d,%d,%d)" % (geo.x(), geo.y(), geo.width(), geo.height())

        def getWindowList():
            wlist = []
            for key, window in self.docks.iteritems():
                if not wlist:
                    wlist.append(key)
                    continue
                for i in range(len(wlist)):
                    geo1 = self.docks[wlist[i]].geometry()
                    geo2 = window.geometry()
                    if geo1.x() > geo2.x():
                        break
                    elif geo1.x() == geo2.x() and geo1.y() > geo2.y():
                        break
                wlist.insert(i, key)

            return wlist

        try:
            if filename:
                outfile = open(filename, "w")
            else:
                outfile = open(environ["config"] + "layout", "w")
        except:
            return

        for key in ["main", "tab"]:
            window = mainWidgets[key]
            outfile.write(key + ":")
            outfile.write("visible=" + str(window.isVisible()) + ";")
            outfile.write("geometry=" + getGeometry(window) + "\n")

        for key in getWindowList():
            window = self.docks[key]
            outfile.write(key + ":")
            outfile.write("visible=" + str(window.isVisible()) + ";")
            outfile.write("floating=" + str(window.isFloating()) + ";")
            outfile.write("location=" + str(window.getLocation()) + ";")
            outfile.write("geometry=" + getGeometry(window) + "\n")

        outfile.write("project:" + self.project)
        outfile.close()

    def loadLayout(self, filename=""):
        """
        Load the layout.
        """
        def parse(text):
            if text == "True":
                return True
            elif text == "False":
                return False
            else:
                areas = [
                    QtCore.Qt.LeftDockWidgetArea,
                    QtCore.Qt.RightDockWidgetArea,
                    QtCore.Qt.TopDockWidgetArea,
                    QtCore.Qt.BottomDockWidgetArea,
                    QtCore.Qt.LeftDockWidgetArea
                ]
                for area in areas:
                    if int(text) == area:
                        return area

        try:
            if filename:
                infile = open(filename, "r")
            else:
                infile = open(environ["config"] + "layout", "r")
        except:
            return

        lines = infile.readlines()

        windows = self.docks.copy()
        windows["main"] = mainWidgets["main"]
        windows["tab"] = mainWidgets["tab"]
        windows["tm"] = mainWidgets["tm"]

        for line in lines:
            name, properties = line.strip().split(":", 1)
            if name == "project":
                self.project = properties
                continue
            window = windows[name]
            for entry in properties.split(";"):
                prop, val = entry.split("=", 1)
                if prop == "visible":
                    window.setVisible(parse(val))
                elif prop == "geometry":
                    x, y, w, h = val.strip("()").split(",", 3)
                    rect = QtCore.QRect(int(x), int(y), int(w), int(h))
                    window.setGeometry(rect)
                elif prop == "floating":
                    floating = parse(val)
                    window.setFloating(floating)
                elif prop == "location":
                    self.addDockWidget(parse(val), window)

    def setVisible(self, visible):
        """
        Set the visibility of the window and the tray.
        """
        QtGui.QMainWindow.setVisible(self, visible)

        if not options["systray"]:
            return

        self.minimizeAction.setEnabled(visible)
        self.maximizeAction.setEnabled(not self.isMaximized())
        self.restoreAction.setEnabled(self.isMaximized() or not visible)
        self.trayIcon.setVisible(not visible)

        if not visible:
            self.showMessage("GINI", "GINI is still running in the background")

    def setIcon(self, icon):
        """
        Set the icon of the tray.
        """
        self.trayIcon.setIcon(icon)
        self.trayIcon.setToolTip("GINI")

    def iconActivated(self, reason):
        """
        Handle mouse events to the system tray.
        """
        if reason == QtGui.QSystemTrayIcon.DoubleClick:
            self.setVisible(not self.isVisible())
        elif reason == QtGui.QSystemTrayIcon.MiddleClick:
            self.showMessage("Middle Click", "You clicked?")

    def showMessage(self, title, message):
        """
        Show a message from the system tray.
        """
        self.trayIcon.showMessage(title,
                                  message, QtGui.QSystemTrayIcon.Information,
                                  15 * 1000)

    def messageClicked(self):
        """
        Handle mouse clicks to the message.
        """
        QtGui.QMessageBox.information(None,
                                      self.tr("Systray"),
                                      self.tr("Goto whatever"))

    def createTrayActions(self):
        """
        Create the right click tray actions.
        """
        self.minimizeAction = QtGui.QAction(self.tr("&Minimize"), self)
        QtCore.QObject.connect(self.minimizeAction,
                               QtCore.SIGNAL("triggered()"),
                               self, QtCore.SLOT("hide()"))

        self.maximizeAction = QtGui.QAction(self.tr("&Maximize"), self)
        QtCore.QObject.connect(self.maximizeAction,
                               QtCore.SIGNAL("triggered()"), self,
                               QtCore.SLOT("showMaximized()"))

        self.restoreAction = QtGui.QAction(self.tr("&Restore"), self)
        QtCore.QObject.connect(self.restoreAction,
                               QtCore.SIGNAL("triggered()"), self,
                               QtCore.SLOT("showNormal()"))

        self.quitAction = QtGui.QAction(self.tr("&Quit"), self)
        QtCore.QObject.connect(self.quitAction,
                               QtCore.SIGNAL("triggered()"),
                               QtGui.qApp, QtCore.SLOT("quit()"))

    def createTrayIcon(self):
        """
        Create the tray icon and menu.
        """
        self.trayIconMenu = QtGui.QMenu(self)
        self.trayIconMenu.setPalette(defaultOptions["palette"])
        self.trayIconMenu.addAction(self.minimizeAction)
        self.trayIconMenu.addAction(self.maximizeAction)
        self.trayIconMenu.addAction(self.restoreAction)
        self.trayIconMenu.addSeparator()
        self.trayIconMenu.addAction(self.quitAction)

        self.trayIcon = QtGui.QSystemTrayIcon(self)
        self.trayIcon.setContextMenu(self.trayIconMenu)


if __name__ == '__main__':
    app = QtGui.QApplication(sys.argv)
    systray = Systray()
    systray.show()
    sys.exit(app.exec_())
