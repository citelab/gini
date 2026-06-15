"""The graphical representation of devices as nodes"""

import sys
import time
import os
import subprocess
from PyQt4 import QtCore, QtGui
from Core.globals import options, environ, mainWidgets, defaultOptions
from Properties import *
from Core.Item import *


class DropItem(QtGui.QGraphicsItem):
    def __init__(self, itemType=None):
        """
        Create a draggable item, which can be dropped into the canvas.
        """
        super(DropItem, self).__init__()
        if itemType:
            self.device_type = itemType

        self.image = QtGui.QImage(environ["images"] + self.device_type + ".gif")
        if self.image.isNull():
            mainWidgets["log"].append("Unknown node type " + str(self.device_type))
            return

        self.setCursor(QtCore.Qt.OpenHandCursor)
        if itemType in unimplementedTypes:
            self.setToolTip(self.device_type.center(13) + "\nImplement me.")
            self.setEnabled(False)
        else:
            self.setToolTip(self.device_type.center(21) + "\nDrag onto the canvas.")

    def paint(self, painter, option, widget):
        """
        Draw the representation.
        """
        painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, options["smoothing"])
        if not self.isEnabled():
            transparency = QtGui.QImage(self.image)
            transparency.fill(QtGui.qRgba(0, 0, 0, 50))
            painter.drawImage(QtCore.QPoint(-self.image.width()/2, -self.image.height()/2), transparency)
        painter.drawImage(QtCore.QPoint(-self.image.width()/2, -self.image.height()/2), self.image)
        device_text = self.device_type
        if options["names"]:
            painter.drawText(QtCore.QRectF(-70, self.image.height()/2, 145, 60), device_text, QtGui.QTextOption(QtCore.Qt.AlignHCenter))

    def boundingRect(self):
        """
        Get the bounding rectangle of the item.
        """
        rect = self.image.rect()
        toR = QtCore.QRectF(rect.left() - rect.width()/2, rect.top() - rect.height()/2, rect.width(), rect.height())
        return toR

    def mousePressEvent(self, event):
        """
        Handle the mouse events on this item.
        """
        if event.button() != QtCore.Qt.LeftButton:
            event.ignore()
            return

        drag = QtGui.QDrag(event.widget())
        mime = QtCore.QMimeData()
        mime.setText(self.device_type)
        drag.setMimeData(mime)

        drag.setPixmap(QtGui.QPixmap.fromImage(self.image))
        drag.setHotSpot(QtCore.QPoint(15, 30))

        drag.start()


class NodeError(Exception):
    pass


class Node(DropItem, Item):
    def __init__(self, itemType=None):
        """
        Create a draggable item for the main scene to represent devices.
        """
        self.edgeList = []
        super(Node, self).__init__(itemType)
        itemTypes = nodeTypes[self.device_type]
        index = self.findNextIndex(itemTypes[self.device_type])

        if index == 0:
            raise NodeError("Index error.")

        name = self.device_type + "_%d" % index
        self.properties = {}
        self.setProperty("Name", name)
        self.setProperty("name", name)
        self.interfaces = []

        self.newPos = QtCore.QPointF()
        self.setFlag(QtGui.QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QtGui.QGraphicsItem.ItemIsSelectable, True)
        self.setZValue(1)
        self.setToolTip(name)

        self.shell = None

        self.status = None
        self.inc = 4
        self.color = QtGui.QColor(0, 0, 0)

        # Context Menu
        self.menu = QtGui.QMenu()
        self.menu.setPalette(defaultOptions["palette"])
        self.menu.addAction("Delete", self.delete)

        scene = mainWidgets["canvas"].scene()
        QtCore.QObject.connect(scene.getTimer(), QtCore.SIGNAL("timeout()"), self.updateColor)

    def findNextIndex(self, index):
        """
        Find the next index for the node's type.
        """
        itemTypes = nodeTypes[self.device_type]
        firstPass = True
        newIndex = index + 1
        if newIndex > 126:
            newIndex = 1
            firstPass = False
        scene = mainWidgets["canvas"].scene()
        while scene.findItem(self.device_type + "_%d" % newIndex) or newIndex == index:
            newIndex += 1
            if newIndex > 126:
                if not firstPass:
                    return 0
                newIndex = 1
                firstPass = False
        itemTypes[self.device_type] = newIndex
        return newIndex

    def setIndex(self, index):
        """
        Set the index of the node.
        """
        itemTypes = nodeTypes[self.device_type]
        if index > itemTypes[self.device_type]:
            itemTypes[self.device_type] = index
        name = self.device_type + "_%d" % index
        self.setProperty("Name", name)
        self.setProperty("name", name)
        self.setToolTip(name)

    def setStatus(self, status):
        """
        Set the status of the node.
        """
        if self.status == status:
            return

        self.status = status

        if not status:
            self.scene().stopRefresh()
        elif status == "attached":
            self.color = QtGui.QColor(0, 255, 0)
        elif status == "detached":
            self.color = QtGui.QColor(255, 255, 0)
        else:
            self.color = QtGui.QColor(255, 0, 0)

        if not options["glowingLights"]:
            self.scene().update()

    def setRouterStats(self, queue, size, rate):
        """
        Update the router stats of the node.
        """
        self.rstatsWindow.updateStats(queue, size, rate)

    def updateColor(self):
        """
        Update the color of the glowing light.
        """
        if not options["glowingLights"] or not self.status:
            return

        currentGreen = self.color.green()
        if currentGreen == 255:
            self.inc = -4
        elif currentGreen == 127:
            self.inc = 4

        currentRed = self.color.red()
        if currentRed == 255:
            self.inc = -4
        elif currentRed == 127:
            self.inc = 4

        if self.status == "attached":
            self.color.setGreen(self.color.green() + self.inc)
        elif self.status == "detached":
            self.color.setGreen(self.color.green() + self.inc)
            self.color.setRed(self.color.red() + self.inc)
        else:
            self.color.setRed(self.color.red() + self.inc)

    def paint(self, painter, option, widget):
        """
        Draw the representation and its name.
        """
        painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, options["smoothing"])
        painter.drawImage(QtCore.QPoint(-self.image.width()/2, -self.image.height()/2), self.image)
        if options["names"]:
            painter.drawText(QtCore.QRectF(-90, self.image.height()/2, 180, 60), self.getProperty("name"), QtGui.QTextOption(QtCore.Qt.AlignHCenter))

        if self.status:
            painter.setBrush(self.color)
            painter.drawEllipse(self.lightPoint, 5, 5)

        painter.setBrush(QtCore.Qt.NoBrush)

        if self.isSelected():
            painter.setPen(QtGui.QPen(QtCore.Qt.black, 1, QtCore.Qt.DashLine))
            painter.drawRect(self.boundingRect())

    def addEdge(self, edge):
        """
        Add a connection with another node.
        """
        self.edgeList.append(edge)
        edge.adjust()

    def removeEdge(self, edge):
        """
        Remove a connection with another node.
        """
        self.edgeList.remove(edge)

    def edges(self):
        """
        Return all of the node's current connections.
        """
        return self.edgeList[:]    # this notation gets a slice of the entire list, effectively cloning it

    def calculateForces(self):
        """
        Calculate the forces to determine movement of the arrange action.
        """
        if not self.scene() or self.scene().mouseGrabberItem() is self:
            self.newPos = self.pos()
            return

        # Sum up all forces pushing this item away.
        xvel = 0.0
        yvel = 0.0
        for item in self.scene().items():
            if not isinstance(item, Node):
                continue

            line = QtCore.QLineF(self.mapFromItem(item, 0, 0), QtCore.QPointF(0, 0))
            dx = line.dx()
            dy = line.dy()
            l = 2.0 * (dx * dx + dy * dy)
            if l > 0:
                xvel += (dx * 150.0) / l
                yvel += (dy * 150.0) / l

        # Now subtract all forces pulling items together.
        weight = (len(self.edgeList) + 1) * 50.0
        for edge in self.edgeList:
            if edge.sourceNode() is self:
                pos = self.mapFromItem(edge.destNode(), 0, 0)
            else:
                pos = self.mapFromItem(edge.sourceNode(), 0, 0)
            xvel += pos.x() / weight
            yvel += pos.y() / weight

        if QtCore.qAbs(xvel) < 0.1 and QtCore.qAbs(yvel) < 0.1:
            xvel = yvel = 0.0

        sceneRect = self.scene().sceneRect()
        self.newPos = self.pos() + QtCore.QPointF(xvel, yvel)
        self.newPos.setX(min(max(self.newPos.x(), sceneRect.left() + 10), sceneRect.right() - 10))
        self.newPos.setY(min(max(self.newPos.y(), sceneRect.top() + 10), sceneRect.bottom() - 10))

    def advance(self):
        """
        Determine if nodes are to be advanced.
        """
        if self.newPos == self.pos():
            return False

        self.setPos(self.newPos)
        return True

    def setPos(self, *args):
        super(Node, self).setPos(*args)
        for edge in self.edgeList:
            edge.adjust()

    def shape(self):
        """
        Get the shape of the node.
        """
        path = QtGui.QPainterPath()
        path.addEllipse(self.boundingRect())
        return path

    def itemChange(self, change, value):
        """
        Handle movement of the node.
        """
        for edge in self.edgeList:
            edge.adjust()
        if change == QtGui.QGraphicsItem.ItemPositionChange:
            mainWidgets["canvas"].itemMoved()

        return QtGui.QGraphicsItem.itemChange(self, change, value)

    def mousePressEvent(self, event):
        """
        Handle mouse press events on the node.
        """
        self.update()
        if event.button() == QtCore.Qt.RightButton:
            mainWidgets["canvas"].connectNode(self)
        else:
            QtGui.QGraphicsItem.mousePressEvent(self, event)

    def update(self):
        super(Node, self).update()
        for edge in self.edgeList:
            edge.adjust()

    def mouseMoveEvent(self, event):
        """
        Handle mouse move events on the node.
        """
        self.update()
        if mainWidgets["main"].isRunning() and \
                event.buttons() == QtCore.Qt.LeftButton:
            if options["moveAlert"]:
                popup = mainWidgets["popup"]
                popup.setWindowTitle("Moving Disabled")
                popup.setText("Cannot move devices in a running topology!")
                popup.show()
            return

        QtGui.QGraphicsItem.mouseMoveEvent(self, event)

    def mouseReleaseEvent(self, event):
        """
        Handle mouse release events on the node.
        """
        self.update()
        QtGui.QGraphicsItem.mouseReleaseEvent(self, event)

        if not mainWidgets["main"].isRunning():
            self.itemChange(0, 0)

    def attach(self):
        """
        Attach to corresponding device on backend.
        """
        pass

    def mouseDoubleClickEvent(self, event):
        """
        Handle mouse double click events on the node.
        """
        if mainWidgets["main"].isRunning():
            self.attach()

    def nudge(self):
        """
        Nudge the node to trigger a sequence of movements.
        """
        pos = self.pos()
        self.newPos.setX(pos.x() + 1)
        self.newPos.setY(pos.y() + 1)
        self.advance()
        self.newPos.setX(pos.x())
        self.newPos.setY(pos.y())
        self.advance()

    def delete(self):
        """
        Delete the node and its edges.
        """
        if mainWidgets["main"].isRunning():
            mainWidgets["log"].append("You cannot delete items from a running topology!")
            return

        from Tutorial import Tutorial
        if isinstance(mainWidgets["canvas"], Tutorial):
            mainWidgets["log"].append("You cannot delete items from the tutorial!")
            return

        for edge in self.edges():
            edge.delete()

        self.scene().removeItem(self)

    def restart(self):
        """
        Restart the running device.
        """
        if not mainWidgets["main"].isRunning():
            mainWidgets["log"].append("You must start the topology first!")
            return

        client = mainWidgets["client"]
        if client:
            client.send("restart " + self.getName())

    def terminate(self):
        """
        Terminate the running device.
        """
        if not mainWidgets["main"].isRunning():
            mainWidgets["log"].append("You must start the topology first!")
            return

        client = mainWidgets["client"]
        if client:
            client.send("terminate " + self.getName())

    def contextMenu(self, pos):
        """
        Pop up the context menu in the location given by pos
        """
        self.menu.exec_(pos)

    def stop(self):
        """
        Handle special stop actions on a stopped topology.
        """
        pass

    def hoverEnterEvent(self, event):
        """
        Handle a hover event over the node.
        """
        pass

    def hoverMoveEvent(self, event):
        pass

    def hoverLeaveEvent(self, event):
        """
        Handle a hover event leaving the node.
        """
        pass

    def toString(self):
        """
        Return a string representation of the graphical node.
        """
        graphical = self.getName() + ":(%f,%f)\n" % (self.pos().x(), self.pos().y())
        logical = ""
        for prop, value in self.properties.iteritems():
            logical += "\t" + prop + ":" + str(value) + "\n"

        return str(graphical + logical)
