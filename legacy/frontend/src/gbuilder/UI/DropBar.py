"""The drag and drop toolbar"""

from PyQt4 import QtCore, QtGui
from Node import *
from Core.globals import options
from Dockable import *


class DropArea(QtGui.QGraphicsView):
    def __init__(self, itemTypes):
        """
        Create a page of dropable nodes.
        """
        super(DropArea, self).__init__()
        self.itemTypes = itemTypes
        self.yRouterDrop = None

        scene = QtGui.QGraphicsScene(self)
        scene.setItemIndexMethod(QtGui.QGraphicsScene.NoIndex)
        self.setScene(scene)
        self.setRenderHint(QtGui.QPainter.Antialiasing)
        self.setTransformationAnchor(QtGui.QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QtGui.QGraphicsView.AnchorViewCenter)
        self.setViewportUpdateMode(self.FullViewportUpdate)

    def refactorLocation(self, location):
        """
        Resize and reposition nodes based on dock location.
        """
        scene = self.scene()
        scene.clear()

        # print location, QtCore.Qt.LeftDockWidgetArea, QtCore.Qt.RightDockWidgetArea
        # TODO: Fix icon spacing here

        if location == QtCore.Qt.LeftDockWidgetArea \
                or location == QtCore.Qt.RightDockWidgetArea:
            last_node = None
            for i in range(len(self.itemTypes)):
                node = DropItem(self.itemTypes[i])
                if last_node:
                    node.setPos(0, last_node.pos().y() + 75)
                else:
                    node.setPos(0, 0)
                scene.addItem(node)
                last_node = node

            self.setSceneRect(-35, -35, 75, last_node.pos().y() + 100)

        else:
            last_node = None
            for i in range(len(self.itemTypes)):
                node = DropItem(self.itemTypes[i])
                if last_node:
                    node.setPos(last_node.pos().x() + 75, 0)
                else:
                    node.setPos(0, 0)
                scene.addItem(node)
                last_node = node

            self.setSceneRect(0, -35, 400, 75)


class DropBar(Dockable):
    def __init__(self, title, parent):
        """
        Create a drag and drop toolbar.
        """
        super(DropBar, self).__init__(title, parent)
        self.parent = parent

        self.toolBox = QtGui.QToolBox()
        self.setWidget(self.toolBox)

        self.commonDropArea = DropArea(commonTypes)
        self.hostDropArea = DropArea(hostTypes.keys())
        self.netDropArea = DropArea(netTypes.keys())

        self.toolBox.addItem(self.commonDropArea, self.tr("&Common Elements"))
        self.toolBox.addItem(self.hostDropArea, self.tr("&Host Elements"))
        self.toolBox.addItem(self.netDropArea, self.tr("&Net Elements"))

        self.connect(self.toolBox,
                     QtCore.SIGNAL("currentChanged(int)"),
                     self.toolChanged)

        self.connect(self, QtCore.SIGNAL("dockLocationChanged(Qt::DockWidgetArea)"), self.locationChanged)

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
        drop_area = self.widget().currentWidget()
        drop_area.refactorLocation(location)
        Dockable.locationChanged(self, location)
