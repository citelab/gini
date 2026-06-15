"""The canvas to create topologies on"""

from Core.globals import options, mainWidgets
from Core.Connection import *
from Core.Item import *


realMnumber = 3
deviceTypes = {"Bridge": Bridge, "Firewall": Firewall, "Hub": Hub, "Router": Router,
               "Subnet": Subnet, "Switch": Switch, "Mach": Mach,
               "OpenFlowController": OpenFlowController, "OVSwitch": OpenVirtualSwitch,
               "Cloud": Cloud}


class View(QtGui.QGraphicsView):
    def __init__(self, parent=None):
        """
        Create the view component of the canvas.
        """
        super(View, self).__init__(parent)

        # Connections
        self.sourceNode = None
        self.line = None

        self.timerId = 0

        self.setRenderHint(QtGui.QPainter.Antialiasing)
        self.setTransformationAnchor(QtGui.QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QtGui.QGraphicsView.AnchorViewCenter)
        self.setViewportUpdateMode(self.FullViewportUpdate)

        self.setWindowTitle(self.tr("Canvas"))

    def itemMoved(self):
        """
        Start the timer for timerEvent.
        """
        if not self.timerId:
            self.timerId = self.startTimer(1000 / 25)

    def timerEvent(self, event):
        """
        Handle a timer event for arranging.
        """
        if not options["elasticMode"]:
            return

        nodes = [item for item in self.scene().items() if isinstance(item, Node)]

        for node in nodes:
            node.calculateForces()

        itemsMoved = False
        for node in nodes:
            if node.advance():
                itemsMoved = True

        if not itemsMoved:
            self.killTimer(self.timerId)
            self.timerId = 0
            if not options["keepElasticMode"]:
                options["elasticMode"] = False

    def wheelEvent(self, event):
        """
        Handle a wheel event for zooming.
        """
        modifiers = event.modifiers()

        # Zoom with control modifier
        if modifiers == QtCore.Qt.ControlModifier:
            self.scaleView(math.pow(2.0, event.delta() / 240.0))
        else:
            QtGui.QGraphicsView.wheelEvent(self, event)

    def drawBackground(self, painter, rect):
        """
        Customize the drawing of the background.
        """
        sceneRect = self.sceneRect()

        # Background image
        background = str(options["background"])
        if background.startswith("(") and background.endswith(")"):
            try:
                r, g, b = background.strip("()").split(",", 2)
                painter.fillRect(rect, QtGui.QBrush(QtGui.QColor(int(r), int(g), int(b))))
            except:
                pass
        else:
            painter.drawImage(rect, QtGui.QImage(options["background"]))
        painter.eraseRect(sceneRect)

        # Shadow
        rightShadow = QtCore.QRectF(sceneRect.right(), sceneRect.top() + 5, 5, sceneRect.height())
        bottomShadow = QtCore.QRectF(sceneRect.left() + 5, sceneRect.bottom(), sceneRect.width(), 5)
        if rightShadow.intersects(rect) or rightShadow.contains(rect):
            painter.fillRect(rightShadow, QtCore.Qt.darkGray)
        if bottomShadow.intersects(rect) or bottomShadow.contains(rect):
            painter.fillRect(bottomShadow, QtCore.Qt.darkGray)

        # Fill
        painter.setBrush(QtCore.Qt.NoBrush)
        painter.drawRect(sceneRect)

        # Grid
        if not options["grid"]:
            return
        try:
            r, g, b = str(options["gridColor"]).strip("()").split(",", 2)
            painter.setPen(QtGui.QColor(int(r), int(g), int(b)))
        except:
            return
        for i in range(int(sceneRect.height() / 50)):
            painter.drawLine(sceneRect.left(), sceneRect.top() + (i+1)*50, sceneRect.right(), sceneRect.top() + (i+1)*50)
        for i in range(int(sceneRect.width() / 50)):
            painter.drawLine(sceneRect.left() + (i+1)*50, sceneRect.top(), sceneRect.left() + (i+1)*50, sceneRect.bottom())
        painter.setPen(QtCore.Qt.black)

    def scaleView(self, scaleFactor):
        """
        Zoom in or out based on scaleFactor.
        """
        factor = self.matrix().scale(scaleFactor, scaleFactor).mapRect(QtCore.QRectF(0, 0, 1, 1)).width()

        if factor < 0.07 or factor > 100:
            return

        self.scale(scaleFactor, scaleFactor)

    def disconnectNode(self):
        """
        Clear the pending connection attempt.
        """
        self.sourceNode = None
        if self.line is not None:
            self.scene().removeItem(self.line)
            self.line = None

    def connectNode(self, node):
        """
        Start a connection attempt from a node.
        """
        if mainWidgets["main"].isRunning():
            return

        self.scene()
        self.sourceNode = node
        self.line = QtGui.QGraphicsLineItem(QtCore.QLineF(node.pos(), node.pos()))
        self.scene().addItem(self.line)

    def createConnection(self, sourceNode, item):
        """
        Create a connection between sourceNode and item.
        """
        con = Connection(sourceNode, item)
        self.scene().addItem(con)
        item.nudge()

    def mouseMoveEvent(self, event):
        """
        Handle mouse movement for connection purposes.
        """
        if self.line is None:
            QtGui.QGraphicsView.mouseMoveEvent(self, event)
            return
        # Draw Connection line and right click drag
        if event.buttons() == QtCore.Qt.RightButton:
            scene = self.scene()
            scene.itemAt(self.mapToScene(event.pos()))
            self.line.setLine(QtCore.QLineF(self.line.line().p1(), self.mapToScene(event.pos())))

    def mouseReleaseEvent(self, event):
        """
        Handle mouse button release for connection and context menu.
        """
        if event.button() == QtCore.Qt.RightButton:
            scene = self.scene()
            item = scene.itemAt(self.mapToScene(event.pos()))

            def validateEdge():

                def isValid(dest, source):
                    if dest.device_type in connection_rule[source.device_type]:
                        if dest.device_type in ["Mach", "Cloud"]:
                            if len(dest.edges()) == 1:
                                return "%s cannot have more than one connection!" % dest.device_type
                        elif dest.device_type == "Subnet":
                            if len(dest.edges()) == 2:
                                return "Subnet cannot have more than two connections!"
                            if source.device_type in ["Switch", "OVSwitch"]:
                                for edge in dest.edges():
                                    if edge.getOtherDevice(dest).device_type in ["Switch", "OVSwitch"]:
                                        return "Subnet cannot have more than one Switch!"
                                for edge in source.edges():
                                    if edge.getOtherDevice(source).device_type == "Subnet":
                                        return "Switch cannot have more than one Subnet!"
                        elif dest.device_type == "Router":
                            if source.device_type == "OpenFlowController":
                                for edge in dest.edges():
                                    if edge.getOtherDevice(dest).device_type == "OpenFlowController":
                                        return "Router cannot have more than one OpenFlow Controller!"
                        return True
                    elif dest.device_type == "Switch" and source.device_type == "OpenFlowController":
                        if dest.getProperty("OVS mode") == "True":
                            return True
                    elif dest.device_type == "OpenFlowController" and source.device_type == "Switch":
                        if source.getProperty("OVS mode") == "True":
                            return True
                    return False

                # Don't create an edge between the same node
                if self.sourceNode == item:
                    return item.contextMenu(event.globalPos())

                # Check for existing edge
                edges = self.sourceNode.edges()
                for edge in edges:
                    if (edge.sourceNode() == self.sourceNode and edge.destNode() == item) \
                            or (edge.sourceNode() == item and edge.destNode() == self.sourceNode):
                        return

                # Check if connection rules are satisfied
                valid = isValid(self.sourceNode, item)
                if valid is True:
                    valid = isValid(item, self.sourceNode)

                # Create the edge
                if valid is True:
                    self.createConnection(self.sourceNode, item)
                else:
                    popup = mainWidgets["popup"]
                    popup.setWindowTitle("Invalid Connection")
                    if valid is False:
                        popup.setText("Cannot connect " + self.sourceNode.device_type + " and " + item.device_type + "!")
                    else:
                        popup.setText(valid)
                    popup.show()

            if self.sourceNode:
                if isinstance(item, Node):
                    validateEdge()
            elif isinstance(item, Node) or isinstance(item, Connection):
                item.contextMenu(event.globalPos())

            self.disconnectNode()

        QtGui.QGraphicsView.mouseReleaseEvent(self, event)


class Canvas(View):
    def dragEnterEvent(self, event):
        """
        Enable receiving of drop items.
        """
        event.setAccepted(True)

    def dropEvent(self, event):
        """
        Handle a drop.
        """
        mime = event.mimeData()
        node = deviceTypes[str(mime.text())]
        try:
            node = node()
        except:
            return

        scene = self.scene()
        scene.addItem(node)

        scenePos = self.mapToScene(event.pos())
        node.setPos(scenePos.x(), scenePos.y())
        self.setFocus()
        scene.update()

    def dragMoveEvent(self, event):
        pass

    def dragLeaveEvent(self, event):
        pass


class Scene(QtGui.QGraphicsScene):
    def __init__(self, parent=None):
        """
        Create a scene for the view.
        """
        QtGui.QGraphicsScene.__init__(self, parent)
        self.target = None
        self.connect(self, QtCore.SIGNAL("selectionChanged()"), self.select)

        self.timer = QtCore.QTimer()
        self.refreshing = False
        self.paused = False
        self.connect(self.timer, QtCore.SIGNAL("timeout()"), self.refresh)

    def getTimer(self):
        """
        Return the timer.
        """
        return self.timer

    def isRefreshing(self):
        return self.refreshing and not self.paused

    def startRefresh(self):
        """
        Start refreshing for running colored lights.
        """
        self.paused = False
        self.refreshing = True
        self.timer.start(50)

    def pauseRefresh(self):
        self.paused = True

    def unpauseRefresh(self):
        self.paused = False

    def refresh(self):
        """
        Refresh for running colored lights.
        """
        if options["glowingLights"] and not self.paused:
            self.update()

        if not self.refreshing:
            self.timer.stop()
            self.clearSelection()
            self.select()
            mainWidgets["tm"].clear()
            mainWidgets["main"].stopped()

    def stopRefresh(self):
        """
        Stop refreshing.
        """
        self.paused = False
        self.refreshing = False

    def itemAt(self, pos):
        """
        Find item by position.
        """
        item = QtGui.QGraphicsScene.itemAt(self, pos)
        if isinstance(item, Node) or isinstance(item, Connection):
            self.clearSelection()
            item.setSelected(True)
        return item

    def findItem(self, name):
        """
        Find item by name.
        """
        from Core.Device import Device
        for item in self.items():
            if not isinstance(item, Device):
                continue
            if item.getName() == name:
                return item

    def select(self):
        """
        Handle selection.
        """
        properties = mainWidgets["properties"]
        properties.clear()
        interfaces = mainWidgets["interfaces"]
        interfaces.clear()
        routes = mainWidgets["routes"]
        routes.clear()
        for item in self.selectedItems():
            properties.setCurrent(item)
            interfaces.setCurrent(item)
            routes.setCurrent(item)
            properties.display()
            if type(item) in ["Router", "Mach", "Cloud"]:
                interfaces.display()
                routes.display()
