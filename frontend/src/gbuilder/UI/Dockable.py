"""A window that can float or dock"""

from PyQt5 import QtGui, QtCore, QtWidgets


class Dockable(QtWidgets.QDockWidget):
    def __init__(self, title='', parent=None):
        """
        Create a dockable window.
        """
        super(Dockable, self).__init__(title, parent)
        self.location = None
        self.chosenSize = None
        self.timer = QtCore.QTimer()

        # Update signal connections to new style
        self.dockLocationChanged.connect(self.locationChanged)
        self.timer.timeout.connect(self.resetMinsize)

    def resetMinsize(self):
        """
        Unlock the minimum size.
        """
        self.timer.stop()
        self.setMinimumSize(0, 0)

    def locationChanged(self, location):
        """
        Save the dock location.
        """
        self.location = location

    def getLocation(self):
        """
        Return the dock location.
        """
        return self.location

    def setGeometry(self, rect):
        """
        Set the window geometry.
        """
        self.chosenSize = QtCore.QSize(rect.width(), rect.height())
        self.setMinimumSize(self.chosenSize)
        self.timer.start(1000)
        QtWidgets.QDockWidget.setGeometry(self, rect)

    def sizeHint(self):
        """
        Provide a size hint.
        """
        if self.chosenSize:
            return self.chosenSize
        else:
            return QtWidgets.QDockWidget.sizeHint(self)
