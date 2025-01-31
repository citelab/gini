"""The drag and drop toolbar"""

from PyQt5 import QtCore, QtGui, QtWidgets
from .Node import *
from Core.globals import (
    options, nodeTypes, commonTypes, unimplementedTypes,
    hostTypes, netTypes, customTypes
)
from .Dockable import *


class DropArea(QtWidgets.QListWidget):
    def __init__(self, types):
        """
        Create a drop area for the specified types.
        """
        super(DropArea, self).__init__()
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QtWidgets.QAbstractItemView.DragOnly)
        self.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)

        for t in types:
            if t not in unimplementedTypes:
                self.addItem(t)

    def refactorLocation(self, location):
        """
        Adjust the view based on dock location.
        """
        if location == QtCore.Qt.LeftDockWidgetArea or location == QtCore.Qt.RightDockWidgetArea:
            self.setViewMode(QtWidgets.QListView.ListMode)
            self.setFlow(QtWidgets.QListView.TopToBottom)
        else:
            self.setViewMode(QtWidgets.QListView.IconMode)
            self.setFlow(QtWidgets.QListView.LeftToRight)
            self.setGridSize(QtCore.QSize(75, 75))


class DropBar(Dockable):
    def __init__(self, title, parent):
        """
        Create a drag and drop toolbar.
        """
        super(DropBar, self).__init__(title, parent)
        self.parent = parent

        self.toolBox = QtWidgets.QToolBox()
        self.setWidget(self.toolBox)

        self.commonDropArea = DropArea(commonTypes)
        self.hostDropArea = DropArea(list(hostTypes.keys()))
        self.netDropArea = DropArea(list(netTypes.keys()))
        self.customDropArea = DropArea(list(customTypes.keys()))

        self.toolBox.addItem(self.commonDropArea, self.tr("&Common Elements"))
        self.toolBox.addItem(self.hostDropArea, self.tr("&Host Elements"))
        self.toolBox.addItem(self.netDropArea, self.tr("&Net Elements"))
        self.toolBox.addItem(self.customDropArea, self.tr("&Custom Elements"))

        # Update signal connections to new style
        self.toolBox.currentChanged.connect(self.toolChanged)
        self.dockLocationChanged.connect(self.locationChanged)

        self.toolChanged(self.toolBox.currentIndex())
        self.setFocusPolicy(QtCore.Qt.NoFocus)

    def toolChanged(self, index):
        """
        Handle the page change.
        """
        widget = self.widget()
        drop_area = widget.currentWidget()
        drop_area.refactorLocation(self.location)

    def locationChanged(self, location):
        """
        Handle the dock location change.
        """
        self.location = location
        for i in range(self.toolBox.count()):
            drop_area = self.toolBox.widget(i)
            if drop_area:
                drop_area.refactorLocation(location)
        Dockable.locationChanged(self, location)
