"""The interactive tutorial for this program"""

from PyQt4 import QtCore, QtGui
from Canvas import *
from Node import *
from Core.Interfaceable import Interfaceable
from Core.globals import mainWidgets
import math


class Arrow(QtGui.QGraphicsItem):
    def __init__(self, angle):
        """
        Create an indicator arrow for the tutorial.
        """
        super(Arrow, self).__init__()
        self.angle = angle
        self.image = QtGui.QImage(environ["images"] + "arrow.png").transformed(self.getRotationMatrix())
        self.timer = QtCore.QTimer()
        self.tl = QtCore.QTimeLine(1000)
        self.tl.setFrameRange(0, 100)
        self.tl.setLoopCount(0)
        self.a = None

    def getRotationMatrix(self):
        """
        Get a rotation matrix based on the angle.
        """
        m11 = math.cos(self.angle)
        m12 = -math.sin(self.angle)
        m21 = math.sin(self.angle)
        m22 = math.cos(self.angle)
        return QtGui.QMatrix(m11, m12, m21, m22, 0, 0)

    def start(self):
        """
        Start the animation of the arrow.
        """
        self.a = QtGui.QGraphicsItemAnimation()
        self.a.setItem(self)
        self.a.setTimeLine(self.tl)
        self.a.setTranslationAt(1, -math.cos(self.angle) * 20, math.sin(self.angle) * 20)
        self.tl.start()

    def stop(self):
        """
        Stop the animation of the arrow.
        """
        self.tl.stop()

    def paint(self, painter, option, widget):
        """
        Draw the arrow.
        """
        painter.drawImage(QtCore.QPoint(-self.image.width()/2, -self.image.height()/2), self.image)

    def boundingRect(self):
        """
        Get the bounding rectangle of the item.
        """
        rect = self.image.rect()
        return QtCore.QRectF(rect.left() - rect.width()/2, rect.top() - rect.height()/2, rect.right(), rect.bottom())


class TextItem(QtGui.QGraphicsTextItem):
    def __init__(self, text):
        """
        Create a fancy text item to display instructions on.
        """
        super(TextItem, self).__init__(text)
        self.shining = True
        self.offset = -50
        self.gradientRect = QtCore.QRectF()
        self.gradientBrush = QtGui.QBrush()
        self.restartTimer = QtCore.QTimer()
        self.offsetTimer = QtCore.QTimer()
        self.connect(self.restartTimer, QtCore.SIGNAL("timeout()"), self.restart)
        self.connect(self.offsetTimer, QtCore.SIGNAL("timeout()"), self.changeOffset)
        self.restartTimer.start(4000)
        self.offsetTimer.start(40)

    def restart(self):
        """
        Move the gradient offset back into the initial position.
        """
        self.offset = -50
        self.shining = True

    def changeOffset(self):
        """
        Shift the gradient gradually.
        """
        if self.shining:
            adjusted = self.boundingRect().adjusted(-5, -5, 5, 5)
            left = adjusted.left() + self.offset
            width = 50
            if left < -5:
                left = -5
            elif left + 50 > adjusted.right():
                width = adjusted.right() - left
            self.gradientRect = QtCore.QRectF(left, adjusted.top(), width, adjusted.height())

            rightGradient = 50 + self.offset
            if rightGradient > adjusted.right() + 50:
                self.shining = False
                return

            gradient = QtGui.QLinearGradient(self.offset, 0, rightGradient, 0)
            self.offset += 8

            gradient.setColorAt(0, QtGui.QColor(160, 160, 160, 128))
            gradient.setColorAt(0.5, QtGui.QColor(255, 255, 255, 128))
            gradient.setColorAt(1, QtGui.QColor(160, 160, 160, 128))
            self.gradientBrush = QtGui.QBrush(gradient)
            self.update()

    def center(self):
        """
        Center the text item.
        """
        cpos = QtCore.QPoint(mainWidgets["canvas"].geometry().center().x(), 5)
        scpos = mainWidgets["canvas"].mapToScene(cpos)
        tpos = scpos - QtCore.QPointF(self.boundingRect().width() / 2, 0)
        self.setPos(tpos)

    def paint(self, painter, option, widget=0):
        """
        Draw the text item.
        """
        self.center()
        color = QtGui.QColor(128, 128, 128, 128)
        adjusted = self.boundingRect().adjusted(-5, -5, 5, 5)
        painter.fillRect(adjusted, color)
        if self.shining:
            painter.fillRect(self.gradientRect, self.gradientBrush)
        painter.fillRect(self.boundingRect(), QtGui.QColor(255, 255, 255, 192))
        QtGui.QGraphicsTextItem.paint(self, painter, option, widget)


class TutorialEdge(Edge):
    def __init__(self, startItem, endItem):
        """
        Create an edge specific to this tutorial.
        """
        super(TutorialEdge, self).__init__(startItem, endItem)
        self.setFlag(QtGui.QGraphicsItem.ItemIsSelectable, False)
        self.p1 = startItem.pos()
        self.p2 = endItem.pos()

    def getEndpoints(self):
        """
        Get the endpoints of this edge.
        """
        return self.p1, self.p2

    def paint(self, painter, option, widget=None):
        """
        Draw the edge.
        """
        painter.setRenderHint(QtGui.QPainter.Antialiasing, options["smoothing"])
        painter.setPen(QtGui.QPen(QtCore.Qt.gray, 1))
        painter.drawLine(self.line())

    def contextMenu(self, pos):
        pass


class TutorialNode(Node):
    def __init__(self, itemType):
        """
        Create a node specific to this tutorial.
        """
        super(TutorialNode, self).__init__(itemType)
        self.setFlag(QtGui.QGraphicsItem.ItemIsMovable, False)
        self.setFlag(QtGui.QGraphicsItem.ItemIsSelectable, False)
        self.setAcceptDrops(True)
        self.dragOver = False

    def paint(self, painter, option, widget):
        """
        Draw the node.
        """
        painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, options["smoothing"])
        painter.drawImage(QtCore.QPoint(-self.image.width()/2, -self.image.height()/2), self.image)
        if not self.dragOver:
            transparency = QtGui.QImage(self.image)
            transparency.fill(QtGui.qRgba(0, 0, 0, 50))
            painter.drawImage(QtCore.QPoint(-self.image.width()/2, -self.image.height()/2), transparency)

        if options["names"]:
            painter.drawText(QtCore.QRectF(-70, self.image.height()/2, 145, 60), self.device_type, QtGui.QTextOption(QtCore.Qt.AlignHCenter))

    def dragEnterEvent(self, event):
        """
        Enable receiving of drop items on the node.
        """
        event.setAccepted(True)
        self.dragOver = True

    def dropEvent(self, event):
        """
        Handle a drop.
        """
        mime = event.mimeData()
        if self.device_type != mime.text():
            self.dragOver = False
            return

        node = deviceTypes[str(mime.text())]
        node = node()

        scene = self.scene()
        scene.addItem(node)
        node.setPos(self.pos())
        node.setFlag(QtGui.QGraphicsItem.ItemIsMovable, False)
        scene.removeItem(self)

        node.setFocus()
        scene.update()
        self.dragOver = False

        for item in scene.items():
            if isinstance(item, TutorialNode):
                return

        mainWidgets["canvas"].next()

    def dragLeaveEvent(self, event):
        """
        Handle a drag event leaving the node.
        """
        self.dragOver = False

    def contextMenu(self, pos):
        pass


class Tutorial(View):
    def __init__(self, parent=None):
        """
        Create an interactive tutorial on how to use this program.
        """
        super(Tutorial, self).__init__(parent)
        self.arrows = []
        self.messages = []
        self.currentArrow = None
        self.text = None
        self.index = -1

        scene = Scene(self)
        scene.setItemIndexMethod(QtGui.QGraphicsScene.NoIndex)
        scene.setSceneRect(-175, -160, 350, 320)
        self.setScene(scene)

        self.timer = QtCore.QTimer()

        self.connect(scene, QtCore.SIGNAL("selectionChanged()"), self.select)
        self.connect(mainWidgets["properties"].model, QtCore.SIGNAL("dataChanged(QModelIndex,QModelIndex)"), self.propertiesChanged)
        self.connect(mainWidgets["interfaces"].rightScroll, QtCore.SIGNAL("clicked()"), self.navigateInterfaces)
        self.connect(mainWidgets["main"].compileAct, QtCore.SIGNAL("triggered()"), self.compile)
        self.connect(self.timer, QtCore.SIGNAL("timeout()"), self.timerExpired)
        self.connect(mainWidgets["main"].startServerAct, QtCore.SIGNAL("triggered()"), self.startServer)
        self.connect(mainWidgets["main"].runAct, QtCore.SIGNAL("triggered()"), self.run)
        self.connect(mainWidgets["main"].stopAct, QtCore.SIGNAL("triggered()"), self.stop)

    def addStep(self, message, angle, pos=QtCore.QPoint()):
        """
        Add a step to the tutorial.
        """
        self.messages.append(message)
        if angle is None:
            self.arrows.append(None)
            return

        arrow = Arrow(angle)
        arrow.setPos(pos)
        self.arrows.append(arrow)

    def start(self):
        """
        Start the tutorial.
        """
        self.addStep("First, fill in the following topology by dragging elements from the\nDrag & Drop Toolbar indicated by the arrow and dropping them into\na spot of the corresponding element type.",
                     math.pi, QtCore.QPointF(-160, 0))
        self.addStep("Next, connect the elements together by holding right-click from a\nsource element, and releasing on top of the destination element.",
                     None)
        self.addStep("Now select the targeted subnet element in the topology.",
                     3 * math.pi / 2, QtCore.QPointF(50, -90))
        self.addStep("This window is used to display and modify item properties.\nModify the 'subnet' property to '192.168.X.0', where X is the subnet ID\nnumber given in the name (Subnet_X).",
                     0, QtCore.QPointF(160, -95))
        self.addStep("Now select the targeted router element in the topology.",
                     math.pi, QtCore.QPointF(60, -80))
        self.addStep("This window will display the properties of a given interface\nof an element such as a router.  Try navigating to the next\ninterface using the right arrow button.",
                     0, QtCore.QPointF(160, 55))
        self.addStep("The toolbar provides easy access to several commands.  The\none we are interested in now is compiling the topology.  Press\nthe compile button now, and be sure to provide a save filename.",
                     3 * math.pi / 4, QtCore.QPointF(155, -155))
        self.addStep("The log window provides messages to let you know if certain\ncommands have been executed properly.  If the compile was\nsuccessful, a message should indicate so.",
                     3 * math.pi / 2, QtCore.QPointF(0, 145))
        self.addStep("The tab name indicates the current project you have open.  The\npurpose of a project is to share Machs between topologies.  The\nproject name determines where the topology data is stored.",
                     3 * math.pi / 4, QtCore.QPointF(-155, -145))
        self.addStep("To run this topology, we will need a server which has the\nbackend installed.  Make sure there is a valid server and host name\nin the server options, and then press the start server button.",
                     3 * math.pi / 4, QtCore.QPointF(155, -155))
        self.addStep("If the server started properly, and the compilation step before\nwas successful, then pressing the run button should start the\ntopology.",
                     3 * math.pi / 4, QtCore.QPointF(155,-155))
        self.addStep("If the topology started properly, you should be able to see some\nstatus lights on certain devices.  The ones that have lights\ncan be attached to by double-clicking on them.  Try one now.",
                     None)
        self.addStep("After you are finished with this topology, press the stop button\nto stop the running topology.",
                     3 * math.pi / 4, QtCore.QPointF(155, -155))
        self.addStep("The tutorial is finished.  Most of the basics have been covered.\nFor more information, please consult the FAQ in the help menu.\nYou can exit the tutorial by closing this topology.",
                     None)

        Mach1 = TutorialNode("Mach")
        Mach2 = TutorialNode("Mach")
        Mach3 = TutorialNode("Mach")
        switch = TutorialNode("Switch")
        router = TutorialNode("Router")
        subnet1 = TutorialNode("Subnet")
        subnet2 = TutorialNode("Subnet")

        scene = self.scene()
        scene.addItem(Mach1)
        scene.addItem(Mach2)
        scene.addItem(Mach3)
        scene.addItem(switch)
        scene.addItem(router)
        scene.addItem(subnet1)
        scene.addItem(subnet2)

        Mach1.setPos(-100, 100)
        Mach2.setPos(0, 100)
        Mach3.setPos(120, 100)
        switch.setPos(-50, 40)
        router.setPos(0, -80)
        subnet1.setPos(-50, -30)
        subnet2.setPos(50, -30)

        scene.addItem(TutorialEdge(Mach1, switch))
        scene.addItem(TutorialEdge(Mach2, switch))
        scene.addItem(TutorialEdge(Mach3, subnet2))
        scene.addItem(TutorialEdge(switch, subnet1))
        scene.addItem(TutorialEdge(subnet1, router))
        scene.addItem(TutorialEdge(subnet2, router))

        self.text = TextItem(QtCore.QString())
        scene.addItem(self.text)
        self.next()

    def next(self):
        """
        Go to the next step.
        """
        if self.currentArrow:
            self.currentArrow.stop()
            self.scene().removeItem(self.currentArrow)

        self.index += 1
        if self.index == len(self.arrows):
            self.currentArrow = None
            return
        self.display()

    def previous(self):
        """
        Go to the previous step.
        """
        if self.currentArrow:
            self.currentArrow.stop()
            self.scene().removeItem(self.currentArrow)
        if self.index == 0:
            self.currentArrow = None
            return
        self.index -= 1
        self.display()

    def display(self):
        """
        Refresh the display with updated arrows and text.
        """
        self.currentArrow = self.arrows[self.index]
        if self.currentArrow:
            self.scene().addItem(self.currentArrow)
            self.currentArrow.start()
        self.text.setPlainText(self.messages[self.index])

    def resizeEvent(self, event):
        """
        Handle a resize event.
        """
        View.resizeEvent(self, event)
        if self.text:
            self.text.center()

    def createConnection(self, sourceNode, destNode):
        """
        Reimplemented connecting as a step in the tutorial.
        """
        if isinstance(sourceNode, TutorialNode) or isinstance(destNode, TutorialNode):
            return

        scene = self.scene()
        con = Connection(sourceNode, destNode)

        p1 = sourceNode.pos()
        p2 = destNode.pos()
        cItems = scene.collidingItems(con)
        for item in cItems:
            if isinstance(item, TutorialEdge):
                endpoints = item.getEndpoints()
                if endpoints == (p1, p2) or endpoints == (p2, p1):
                    scene.removeItem(item)
                    scene.addItem(con)
                    destNode.nudge()
                    break

        for item in scene.items():
            if isinstance(item, TutorialEdge):
                return

        self.next()

    def select(self):
        """
        Handle selection for several steps in the tutorial.
        """
        if self.index == 2:
            for item in self.scene().selectedItems():
                if item.device_type == "Subnet":
                    self.next()
                    return
        elif self.index == 3:
            for item in self.scene().selectedItems():
                if item.device_type != "Subnet":
                    self.previous()
                    return
        elif self.index == 4:
            for item in self.scene().selectedItems():
                if item.device_type == "Router":
                    self.next()
                    return
        elif self.index == 5:
            for item in self.scene().selectedItems():
                if item.device_type != "Router":
                    self.previous()
                    return

    def propertiesChanged(self, index, index2):
        """
        Handle property changes for a step in the tutorial.
        """
        if self.index != 3:
            return

        for item in self.scene().selectedItems():
            if item.device_type == "Subnet":
                subnet = "192.168.%d.0" % item.getID()
                if item.getProperty("subnet") == subnet:
                    self.next()
                    return

    def navigateInterfaces(self):
        """
        Handle interface navigation as a step in the tutorial.
        """
        if self.index != 5:
            return

        self.next()

    def compile(self):
        """
        Handle compiling as a step in the tutorial.
        """
        if self.index != 6:
            return

        if not mainWidgets["main"].filename:
            return

        self.next()
        self.timer.start(10000)

    def timerExpired(self):
        """
        Handle timed steps in the tutorial.
        """
        if self.index == 8:
            self.timer.stop()

        self.next()

    def startServer(self):
        """
        Handle starting the server as a step in the tutorial.
        """
        if self.index != 9:
            return

        self.next()

    def run(self):
        """
        Handle running a topology as a step in the tutorial.
        """
        if self.index != 10:
            return

        self.next()

    def mouseDoubleClickEvent(self, event):
        """
        Handle attaching as a step in the tutorial.
        """
        pos = self.mapToScene(event.pos())
        item = self.scene().itemAt(pos)
        if isinstance(item, Interfaceable):
            item.attach()
            if self.index == 11:
                self.next()

    def stop(self):
        """
        Handle stopping a running topology as a step in the tutorial.
        """
        if self.index != 12:
            return

        self.next()
        mainWidgets["main"].unlockDocks()
