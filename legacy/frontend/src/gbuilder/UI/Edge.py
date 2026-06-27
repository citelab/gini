"""The graphical representation of connections of nodes"""

import math
from PyQt4 import QtCore, QtGui
from Core.Item import *
from Core.globals import options, mainWidgets, defaultOptions


class Edge(QtGui.QGraphicsLineItem, Item):
    def __init__(self, startItem, endItem, parent=None, scene=None):
        """
        Create an edge between two nodes, linking them together graphically.
        """
        super(Edge, self).__init__(parent, scene)

        self.source = startItem
        self.dest = endItem
        self.sourcePoint = QtCore.QPointF()
        self.destPoint = QtCore.QPointF()
        self.source.addEdge(self)
        self.dest.addEdge(self)
        self.properties = {}
        self.setProperty("id", "SomeEdge")
        self.interfaces = []

        self.setPen(QtGui.QPen(QtCore.Qt.black, 2, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap, QtCore.Qt.RoundJoin))
        self.setFlag(QtGui.QGraphicsItem.ItemIsSelectable, True)

        self.adjust()

    def boundingRect(self):
        """
        Get the bounding rectangle of the edge.
        """
        extra = (self.pen().width() + 20) / 2.0
        p1 = self.line().p1()
        p2 = self.line().p2()
        return QtCore.QRectF(p1, QtCore.QSizeF(p2.x() - p1.x(), p2.y() - p1.y())).normalized().adjusted(-extra, -extra, extra, extra)

    def sourceNode(self):
        """
        Get the source node.
        """
        return self.source

    def setSourceNode(self, node):
        """
        Set the source node.
        """
        self.source = node
        self.adjust()

    def destNode(self):
        """
        Get the destination node.
        """
        return self.dest

    def setDestNode(self, node):
        """
        Set the destination node.
        """
        self.dest = node
        self.adjust()

    def shape(self):
        """
        Get the shape of the edge.
        """
        return QtGui.QGraphicsLineItem.shape(self)

    def adjust(self):
        """
        Adjust length and angle of edge based on movement of nodes.
        """
        if not self.source or not self.dest:
            return

        line = QtCore.QLineF(self.mapFromItem(self.source, 0, 0), self.mapFromItem(self.dest, 0, 0))
        self.setLine(line)

        length = line.length()

        if length == 0.0:
            return

        edgeOffset = QtCore.QPointF((line.dx() * 20) / length, (line.dy() * 20) / length)

        self.prepareGeometryChange()
        self.sourcePoint = line.p1() + edgeOffset
        self.destPoint = line.p2() - edgeOffset

    def paint(self, painter, option, widget=None):
        """
        Draw the representation.
        """
        if self.source.collidesWithItem(self.dest):
            return
        painter.setRenderHint(QtGui.QPainter.Antialiasing, options["smoothing"])

        painter.drawLine(self.line())

        if self.isSelected():
            painter.setPen(QtGui.QPen(QtCore.Qt.black, 1, QtCore.Qt.DashLine))

            baseLine = QtCore.QLineF(0,0,1,0)
            myLine = QtCore.QLineF(self.line())
            angle = math.radians(myLine.angle(baseLine))
            myLine.translate(4.0 * math.sin(angle), 4.0 * math.cos(angle))
            painter.drawLine(myLine)
            myLine.translate(-8.0 * math.sin(angle), -8.0 * math.cos(angle))
            painter.drawLine(myLine)

    def delete(self):
        """
        Delete the edge and remove it from its nodes.
        """
        if mainWidgets["main"].isRunning():
            mainWidgets["log"].append("You cannot delete items from a running topology!")
            return

        from UI.Tutorial import Tutorial
        if isinstance(mainWidgets["canvas"], Tutorial):
            mainWidgets["log"].append("You cannot delete items from the tutorial!")
            return

        self.source.removeEdge(self)
        self.dest.removeEdge(self)
        self.scene().removeItem(self)

    def contextMenu(self, pos):
        """
        Pop up the context menu on right click.
        """
        self.menu = QtGui.QMenu()
        self.menu.setPalette(defaultOptions["palette"])
        self.menu.addAction("Delete", self.delete)
        self.menu.exec_(pos)

    def toString(self):
        """
        Return a string representation of the graphical edge.
        """
        graphical = "edge:(" + self.source.getName() + "," + self.dest.getName() + ")\n"
        logical = ""
        for prop, value in self.properties.iteritems():
            logical += "\t" + prop + ":" + value + "\n"

        return graphical + logical
