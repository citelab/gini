"""The system tray used by the main window"""

import sys, os
from PyQt5 import QtCore, QtGui, QtWidgets
from Core.globals import *
from .Tutorial import Tutorial


class Systray(QtWidgets.QMainWindow):
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

        self.trayIcon.activated.connect(self.iconActivated)

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

    def getWindowList(self):
        """
        Get a list of window names.
        """
        for key, window in self.docks.items():
            if window.isVisible():
                yield key

    def saveLayout(self, filename=""):
        """
        Save the current layout to a file.
        """
        def getGeometryString(window):
            geo = window.geometry()
            return "(%d,%d,%d,%d)" % (geo.x(), geo.y(), geo.width(), geo.height())

        try:
            if filename:
                file = open(filename, "w")
            else:
                file = open(environ["config"] + "layout", "w")

            for key in self.getWindowList():
                window = self.docks[key]
                geometry = window.geometry()
                file.write(key + ":" + 
                          str(geometry.x()) + "," + 
                          str(geometry.y()) + "," + 
                          str(geometry.width()) + "," + 
                          str(geometry.height()) + "," + 
                          str(window.isFloating()) + "\n")
            file.close()
        except:
            print("Failed to save layout")

    def loadLayout(self, filename=""):
        """
        Load a saved layout from a file.
        """
        try:
            if filename:
                file = open(filename, "r")
            else:
                file = open(environ["config"] + "layout", "r")
        except:
            return

        for line in file:
            try:
                # New format: key:x,y,width,height,floating
                key, geometry = line.strip().split(":")
                if not key in self.docks:
                    continue
                
                x, y, width, height, floating = geometry.split(",")
                window = self.docks[key]
                
                # Create QRect for geometry
                rect = QtCore.QRect(int(x), int(y), int(width), int(height))
                
                # Set window geometry and state
                window.setGeometry(rect)
                window.setFloating(floating.lower() == "true")
                
            except Exception as e:
                print(f"Error loading layout entry: {e}")
                continue

        file.close()

    def setVisible(self, visible):
        """
        Set the visibility of the window and the tray.
        """
        QtWidgets.QMainWindow.setVisible(self, visible)

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
        QtWidgets.QMessageBox.information(None,
                                      self.tr("Systray"),
                                      self.tr("Goto whatever"))

    def createTrayActions(self):
        """
        Create the right click tray actions.
        """
        self.minimizeAction = QtWidgets.QAction(self.tr("&Minimize"), self)
        self.minimizeAction.triggered.connect(self.hide)

        self.maximizeAction = QtWidgets.QAction(self.tr("&Maximize"), self)
        self.maximizeAction.triggered.connect(self.showMaximized)

        self.restoreAction = QtWidgets.QAction(self.tr("&Restore"), self)
        self.restoreAction.triggered.connect(self.showNormal)

        self.quitAction = QtWidgets.QAction(self.tr("&Quit"), self)
        self.quitAction.triggered.connect(self.quit)

    def createTrayIcon(self):
        """
        Create the tray icon and menu.
        """
        self.trayIconMenu = QtWidgets.QMenu(self)
        self.trayIconMenu.setPalette(defaultOptions["palette"])
        self.trayIconMenu.addAction(self.minimizeAction)
        self.trayIconMenu.addAction(self.maximizeAction)
        self.trayIconMenu.addAction(self.restoreAction)
        self.trayIconMenu.addSeparator()
        self.trayIconMenu.addAction(self.quitAction)

        self.trayIcon = QtWidgets.QSystemTrayIcon(self)
        self.trayIcon.setContextMenu(self.trayIconMenu)


if __name__ == '__main__':
    app = QtWidgets.QApplication(sys.argv)
    systray = Systray()
    systray.show()
    sys.exit(app.exec_())
