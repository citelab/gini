"""The stats window to display wireless stats"""

from PyQt4 import QtCore, QtGui
from Core.globals import mainWidgets
from Dockable import *


class StatsWindow(Dockable):
    def __init__(self, name, parent=None):
        """
        Create a stats window to view mobile statistics.
        """
        super(StatsWindow, self).__init__(name + "stats", parent)

        self.name = name
        self.statsText = QtGui.QTextEdit(self)
        self.statsText.setReadOnly(True)
        self.setWidget(self.statsText)

        self.resize(200, 100)
        self.setFloating(True)

        self.timer = QtCore.QTimer()
        self.connect(self.timer, QtCore.SIGNAL("timeout()"), self.requestStats)

    def requestStats(self):
        """
        Request stats from the server.
        """
        if mainWidgets["main"].isRunning():
            client = mainWidgets["client"]
            if client:
                client.send("wstats " + self.name)
        else:
            self.hide()

    def updateStats(self, stats):
        """
        Receive updated stats from the server.
        """
        self.statsText.setText(stats)

    def show(self):
        """
        Show the window.
        """
        self.requestStats()
        self.timer.start(1000)
        QtGui.QDockWidget.show(self)

    def hide(self):
        """
        Hide the window.
        """
        self.timer.stop()
        self.statsText.clear()
        QtGui.QDockWidget.hide(self)
