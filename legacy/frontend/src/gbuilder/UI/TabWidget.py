"""The project tab widget to hold the canvas"""

from PyQt4 import QtCore, QtGui


class TabWidget(QtGui.QTabWidget):
    def __init__(self, parent=None):
        """
        Create a project tab widget.
        """
        super(TabWidget, self).__init__(parent)
        self.chosenSize = None
        self.setMinimumSize(400, 300)
        self.timer = QtCore.QTimer()

        self.connect(self.timer, QtCore.SIGNAL("timeout()"), self.resetMinsize)

    def resetMinsize(self):
        """
        Unlock the minimum size.
        """
        self.timer.stop()
        self.setMinimumSize(400, 300)

    def setGeometry(self, rect):
        """
        Set the widget geometry.
        """
        self.chosenSize = QtCore.QSize(rect.width(), rect.height())
        self.setMinimumSize(self.chosenSize)
        self.timer.start(1000)
        QtGui.QTabWidget.setGeometry(self, rect)

    def sizeHint(self):
        """
        Provide a size hint.
        """
        if self.chosenSize:
            return self.chosenSize
        else:
            return QtGui.QTabWidget.sizeHint(self)
