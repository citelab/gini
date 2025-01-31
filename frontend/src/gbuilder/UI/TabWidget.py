"""The project tab widget to hold the canvas"""

from PyQt5 import QtCore, QtGui, QtWidgets


class TabWidget(QtWidgets.QTabWidget):
    def __init__(self, parent=None):
        """
        Create a project tab widget.
        """
        super(TabWidget, self).__init__(parent)
        self.chosenSize = None
        self.setMinimumSize(400, 300)
        self.timer = QtCore.QTimer()

        self.timer.timeout.connect(self.resetMinsize)

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
        QtWidgets.QTabWidget.setGeometry(self, rect)

    def sizeHint(self):
        """
        Provide a size hint.
        """
        if self.chosenSize:
            return self.chosenSize
        else:
            return QtWidgets.QTabWidget.sizeHint(self)
