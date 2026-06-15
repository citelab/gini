"""The task manager window to display and kill processes"""

from PyQt4 import QtGui, QtCore
from Core.globals import mainWidgets
from Dockable import *


class TaskManagerWindow(Dockable):
    def __init__(self, parent=None):
        """
        Create a task manager window.
        """
        super(TaskManagerWindow, self).__init__(parent=parent)

        self.widget = QtGui.QWidget()
        self.layout = QtGui.QVBoxLayout()
        self.list = QtGui.QListWidget()
        self.button = QtGui.QPushButton("Kill")
        self.layout.addWidget(self.list)
        self.layout.addWidget(self.button)
        self.widget.setLayout(self.layout)

        self.setWidget(self.widget)

        self.connect(self.button, QtCore.SIGNAL("clicked()"), self.kill)
        self.connect(self,
                     QtCore.SIGNAL("topLevelChanged(bool)"),
                     self.dockChanged)

    def dockChanged(self, floating):
        """
        Handle a change in the dock location or state.
        """
        if floating:
            self.setWindowOpacity(0.8)

    def kill(self):
        """
        Kill the selected process.
        """
        for item in self.list.selectedItems():
            entry = str(item.text())
            device, pid, status = entry.split("\t", 2)
            client = mainWidgets["client"]
            if client:
                client.send("kill " + pid)

    def update(self, device, pid, status):
        """
        Update the pid and status of a device in the list.
        """
        entries = self.list.findItems(device+"\t", QtCore.Qt.MatchStartsWith)
        entry = device + "\t" + pid + "\t" + status
        if entries:
            if entry == entries[0]:
                return
            entries[0].setText(entry)
        else:
            self.list.addItem(entry)

        mainWidgets["main"].updateProgressBar()

    def getPID(self, device):
        """
        Get the pid from a device in the list.
        """
        entries = self.list.findItems(device+"\t", QtCore.Qt.MatchStartsWith)
        if entries:
            device, pid, status = str(entries[0].text()).split("\t", 2)
            return pid
        else:
            return False

    def clear(self):
        """
        Clear the list.
        """
        self.list.clear()
