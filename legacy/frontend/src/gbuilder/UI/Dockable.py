"""A window that can float or dock"""

from PyQt4 import QtGui, QtCore


class Dockable(QtGui.QDockWidget):
    def __init__(self, title=QtCore.QString(), parent=None):
        """
        Create a dockable window.
        """
        super(Dockable, self).__init__(title, parent)
        self.location = None
        self.chosenSize = None
        self.timer = QtCore.QTimer()

        self.connect(self, QtCore.SIGNAL("dockLocationChanged(Qt::DockWidgetArea)"), self.locationChanged)
        self.connect(self.timer, QtCore.SIGNAL("timeout()"), self.resetMinsize)

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
        QtGui.QDockWidget.setGeometry(self, rect)

    def sizeHint(self):
        """
        Provide a size hint.
        """
        if self.chosenSize:
            return self.chosenSize
        else:
            return QtGui.QDockWidget.sizeHint(self)
