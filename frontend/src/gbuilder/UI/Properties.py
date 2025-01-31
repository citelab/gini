"""The properties window for display item properties"""

from PyQt5 import QtCore, QtGui, QtWidgets
from Core.globals import mainWidgets
from .Dockable import *
from .PropertyComboBox import PropertyComboBox


class ConnectM:
    def __init__(self, name, ip, mac, port):
        self.name = name
        self.ip = ip
        self.mac = mac
        self.port = port
        self.alist = {"m1": ConnectM("m1", "ip1", "mac1", "port1"),
                      "m2": ConnectM("m1", "ip2", "mac2", "port2")}


class PropertyCheckBox(QtWidgets.QCheckBox):
    def __init__(self, item, prop, parent=None):
        super(PropertyCheckBox, self).__init__(parent)
        self.item = item
        self.prop = prop
        self.stateChanged.connect(self.changeState)

    def changeState(self, state):
        if state:
            self.item.setProperty(self.prop, "True")
        else:
            self.item.setProperty(self.prop, "False")


class PropertiesWindow(QtWidgets.QDockWidget):
    def __init__(self, parent=None):
        """
        Create a properties window.
        """
        super(PropertiesWindow, self).__init__(parent)
        
        # Initialize the table
        self.table = QtWidgets.QTableWidget()
        self.table.setColumnCount(2)
        self.table.setHorizontalHeaderLabels(["Property", "Value"])
        self.table.setColumnWidth(0, 100)
        self.table.setColumnWidth(1, 100)
        header = self.table.horizontalHeader()
        header.setStretchLastSection(True)

        # Set up the model
        self.model = QtGui.QStandardItemModel()
        self.model.setHorizontalHeaderLabels(["Property", "Value"])
        
        # Connect signals
        self.table.itemChanged.connect(self.changed)

    def addProperty(self, prop, value, editable=True, checkable=False, combo=False, enabled=True):
        """
        Add a property to display in the window.
        """
        pr = QtGui.QStandardItem(prop)
        pr.setEditable(False)

        val = QtGui.QStandardItem(value)
        if not (checkable or combo):
            val.setEditable(False)
        if prop == "id":
            self.model.insertRow(0, [pr, val])
        elif prop == "name":
            pass
        else:
            self.model.appendRow([pr, val])
            if checkable:
                index = self.model.indexFromItem(val)
                checkbox = PropertyCheckBox(self.currentItem, prop)
                checkbox.setEnabled(enabled)
                if mainWidgets["main"].isRunning():
                    checkbox.setEnabled(False)
                self.view.setIndexWidget(index, checkbox)
                if value == "True":
                    checkbox.setChecked(True)

        if combo:
            index = self.model.indexFromItem(val)
            combobox = PropertyComboBox(self.currentItem, self, prop, value)
            selectedIndex = combobox.findText(self.currentItem.properties[prop])
            combobox.setCurrentIndex(selectedIndex)
            self.view.setIndexWidget(index, combobox)
            combobox.currentIndexChanged.connect(combobox.comboBoxChanged)

    def changed(self, item):
        """
        Handle changes to properties.
        """
        if not self.currentItem or item.column() != 1:
            return

        prop = self.model.item(item.row(), 0).text()
        value = item.text()

        self.currentItem.setProperty(prop, value)
        if prop == "Name":
            self.currentItem.updateToolTip()

    def display(self, item):
        """
        Display the properties of the specified item.
        """
        self.currentItem = item
        self.model.removeRows(0, self.model.rowCount())

        if not item:
            return

        properties = item.getProperties()
        for prop, value in properties.items():
            propItem = QtGui.QStandardItem(prop)
            propItem.setEditable(False)
            valueItem = QtGui.QStandardItem(value)
            self.model.appendRow([propItem, valueItem])

    def clear(self):
        """
        Clear the properties window.
        """
        self.currentItem = None
        self.model.removeRows(0, self.model.rowCount())


class InterfacesWindow(PropertiesWindow):
    def __init__(self, parent):
        """
        Create an interfaces window to store interfaces.
        """
        super(InterfacesWindow, self).__init__(parent)
        
        self.interfaces = {}
        self.current = None
        self.running = False

    def clear(self):
        """
        Clear all interfaces from the window.
        """
        # Replace removeRows() with proper table clearing
        self.table.setRowCount(0)
        self.interfaces.clear()
        self.current = None

    def setCurrent(self, item):
        """
        Set the current item and display after.
        """
        PropertiesWindow.setCurrent(self, item)
        self.display()

    def addProperty(self, prop, value):
        """
        Add a property to display in the window.
        """
        editable = True
        if prop == "routing":
            return
        elif prop == "target":
            value = value.getName()
            editable = False

        PropertiesWindow.addProperty(self, prop, value, editable)

    def scrollLeft(self):
        """
        Scroll to the previous interface of the current item.
        """
        if not self.currentItem:
            return
        from Core.Interfaceable import Interfaceable
        if not isinstance(self.currentItem, Interfaceable):
            return

        if self.currentInterface == 1:
            return

        self.display(-1)

    def scrollRight(self):
        """
        Scroll to the next interface of the current item.
        """
        if not self.currentItem:
            return
        from Core.Interfaceable import Interfaceable
        if not isinstance(self.currentItem, Interfaceable):
            return

        if self.currentInterface == len(self.currentItem.getInterfaces()):
            return

        self.display(1)

    def display(self, inc=0):
        """
        Show the properties of the interface of the current item.
        Which interface is shown depends on inc, of which -1 is the previous,
        0 is the current, and 1 is the next.
        """
        if not self.currentItem:
            return
        from Core.Interfaceable import Interfaceable
        if not isinstance(self.currentItem, Interfaceable):
            return
        interfaces = self.currentItem.getInterfaces()
        if not interfaces:
            return

        self.removeRows()
        self.currentInterface += inc
        interface = interfaces[self.currentInterface-1]
        self.setWindowTitle("Interface %d" % self.currentInterface)
        for prop, value in interface.iteritems():
            self.addProperty(prop, value)

    def changed(self, index, index2):
        """
        Handle a change in the interface properties of the current item.
        """
        value = self.model.data(index)
        propertyIndex = self.model.index(index.row(), index.column()-1)
        prop = self.model.data(propertyIndex)
        self.currentItem.setInterfaceProperty(prop.toString(), value.toString(), index=self.currentInterface - 1)

    def getCurrent(self):
        """
        Return the current item.
        """
        return self.currentItem


class RoutesWindow(PropertiesWindow):
    def __init__(self, interfaces, parent=None):
        """
        Create a routes window to store routes.
        """
        super(RoutesWindow, self).__init__(parent)
        
        self.interfaces = interfaces
        self.currentItem = None
        self.currentInterface = 1
        self.currentRoute = 1
        
        # Create UI elements
        self.leftScroll = QtWidgets.QPushButton("<")
        self.rightScroll = QtWidgets.QPushButton(">")
        self.upScroll = QtWidgets.QPushButton("^")
        self.downScroll = QtWidgets.QPushButton("v")
        
        # Create layouts
        scrollLayout = QtWidgets.QHBoxLayout()
        scrollLayout.addWidget(self.leftScroll)
        scrollLayout.addWidget(self.rightScroll)
        scrollLayout.addWidget(self.upScroll)
        scrollLayout.addWidget(self.downScroll)
        
        mainLayout = QtWidgets.QVBoxLayout()
        mainLayout.addWidget(self.table)
        mainLayout.addLayout(scrollLayout)
        
        # Create central widget
        self.widget = QtWidgets.QWidget()
        self.widget.setLayout(mainLayout)
        self.setWidget(self.widget)
        
        # Connect signals
        self.leftScroll.clicked.connect(self.decInterface)
        self.rightScroll.clicked.connect(self.incInterface)
        self.upScroll.clicked.connect(self.decRoute)
        self.downScroll.clicked.connect(self.incRoute)

    def decRoute(self):
        """
        Decrement the current route number.
        """
        if not self.currentItem:
            return
        if self.currentRoute > 1:
            self.currentRoute -= 1
            self.display()

    def incRoute(self):
        """
        Increment the current route number.
        """
        if not self.currentItem:
            return
        routes = self.currentItem.getInterfaces()[self.currentInterface-1].get("routing", [])
        if self.currentRoute < len(routes):
            self.currentRoute += 1
            self.display()

    def decInterface(self):
        """
        Decrement the current interface number.
        """
        if not self.currentItem:
            return
        if self.currentInterface > 1:
            self.currentInterface -= 1
            self.currentRoute = 1
            self.display()

    def incInterface(self):
        """
        Increment the current interface number.
        """
        if not self.currentItem:
            return
        if self.currentInterface < len(self.currentItem.getInterfaces()):
            self.currentInterface += 1
            self.currentRoute = 1
            self.display()

    def display(self, interfaceInc=0, routeInc=0):
        """
        Show the properties of the interface of the current item.
        Which interface is shown depends on inc, of which -1 is the previous,
        0 is the current, and 1 is the next.
        """
        if not self.currentItem:
            return
        from Core.Interfaceable import Interfaceable
        if not isinstance(self.currentItem, Interfaceable):
            return
        interfaces = self.currentItem.getInterfaces()
        if not interfaces:
            return

        self.removeRows()
        self.currentInterface += interfaceInc
        self.currentRoute += routeInc

        routes = interfaces[self.currentInterface-1][QtCore.QString("routing")]
        if not routes:
            return

        route = routes[self.currentRoute-1]
        self.setWindowTitle("Route %d" % self.currentRoute)
        for prop, value in route.iteritems():
            self.addProperty(prop, value)

    def scrollLeft(self):
        """
        Scroll to the previous route of the current item.
        """
        if not self.currentItem:
            return
        from Core.Interfaceable import Interfaceable
        if not isinstance(self.currentItem, Interfaceable):
            return

        if self.currentRoute == 1:
            return

        self.display(0, -1)

    def scrollRight(self):
        """
        Scroll to the next route of the current item.
        """
        if not self.currentItem:
            return
        from Core.Interfaceable import Interfaceable
        if not isinstance(self.currentItem, Interfaceable):
            return

        interfaces = self.currentItem.getInterfaces()
        if not interfaces:
            return

        routes = interfaces[self.currentInterface-1][QtCore.QString("routing")]
        if not routes:
            return

        if self.currentRoute == len(routes):
            return

        self.display(0, 1)

    def changed(self, index, index2):
        """
        Handle a change in the interface properties of the current item.
        """
        value = self.model.data(index)
        propertyIndex = self.model.index(index.row(), index.column()-1)
        prop = self.model.data(propertyIndex)

        interfaces = self.currentItem.getInterfaces()
        routes = interfaces[self.currentInterface-1][QtCore.QString("routing")]
        if not routes:
            return

        route = routes[self.currentRoute-1]
        route[prop.toString()] = value.toString()
