"""The properties window for display item properties"""

from PyQt4 import QtCore, QtGui
from Core.globals import mainWidgets
from Dockable import *
from PropertyComboBox import PropertyComboBox


class ConnectM:
    def __init__(self, name, ip, mac, port):
        self.name = name
        self.ip = ip
        self.mac = mac
        self.port = port
        self.alist = {"m1": ConnectM("m1", "ip1", "mac1", "port1"),
                      "m2": ConnectM("m1", "ip2", "mac2", "port2")}


class PropertyCheckBox(QtGui.QCheckBox):
    def __init__(self, item, prop, parent=None):
        super(PropertyCheckBox, self).__init__(parent)
        self.item = item
        self.prop = prop
        self.connect(self, QtCore.SIGNAL("stateChanged(int)"), self.changeState)

    def changeState(self, state):
        if state:
            self.item.setProperty(self.prop, "True")
        else:
            self.item.setProperty(self.prop, "False")


class PropertiesWindow(Dockable):
    def __init__(self, parent=None):
        """
        Create a properties window to display properties of selected items.
        """
        super(PropertiesWindow, self).__init__(parent=parent)
        self.createView()
        self.setWidget(self.sourceView)

    def createView(self):
        """
        Create the view and model of the window.
        """
        self.currentItem = None
        self.sourceView = QtGui.QTreeView()

        self.model = QtGui.QStandardItemModel(0, 2, self)
        self.model.setHeaderData(0, QtCore.Qt.Horizontal, QtCore.QVariant("Property"), QtCore.Qt.DisplayRole)
        self.model.setHeaderData(1, QtCore.Qt.Horizontal, QtCore.QVariant("Value"))

        self.sourceView.setEditTriggers(QtGui.QAbstractItemView.CurrentChanged)
        self.sourceView.setModel(self.model)

        self.connect(self,
                     QtCore.SIGNAL("topLevelChanged(bool)"),
                     self.dockChanged)
        self.connect(self.model, QtCore.SIGNAL("dataChanged(QModelIndex,QModelIndex)"), self.changed)


    def addProperty(self, prop, value, editable=True, checkable=False, combo=False, enabled=True):
        """
        Add a property to display in the window.
        """
        pr = QtGui.QStandardItem()
        pr.setData(QtCore.QVariant(prop), QtCore.Qt.DisplayRole)
        pr.setEditable(False)

        val = QtGui.QStandardItem()
        if not (checkable or combo):
            val.setData(QtCore.QVariant(value), QtCore.Qt.EditRole)

        if mainWidgets["main"].isRunning():
            val.setEnabled(False)
        elif not editable:
            val.setEditable(False)
        if prop == "id":
            self.model.insertRow(0, [pr, val])
	    #TODO: find the bug that generated "Name" and "name" (The following 2 lines for temp use)
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
                self.sourceView.setIndexWidget(index, checkbox)
                if value == "True":
                    checkbox.setChecked(True)

        if combo:
            index = self.model.indexFromItem(val)
            combobox = PropertyComboBox(self.currentItem, self, prop, value)
            selectedIndex = combobox.findText(self.currentItem.properties[prop])
            combobox.setCurrentIndex(selectedIndex)
            self.sourceView.setIndexWidget(index, combobox)
            combobox.currentIndexChanged.connect(combobox.comboBoxChanged)



    def changed(self, index, index2):
        """
        Handle a change in the properties of the current item.
        """
        value = self.model.data(index)
        propertyIndex = self.model.index(index.row(), index.column()-1)
        prop = self.model.data(propertyIndex)
        if prop.toString() == "id":
            name = str(value.toString())
            if name.find(self.currentItem.device_type + "_") == 0:
                try:
                    devType, index = name.rsplit("_", 1)
                    index = int(index)
                    if index - 1 in range(126) and mainWidgets["canvas"].scene().findItem(name) is not None:
                        self.currentItem.setIndex(index)
                        return
                except:
                    pass

            popup = mainWidgets["popup"]
            popup.setWindowTitle("Invalid Name Change")
            popup.setText("Only the index of the name can be changed!  The index must be unique and in the range 1-126.")
            popup.show()
        else:
            self.currentItem.setProperty(prop.toString(), value.toString())

    def dockChanged(self, floating):
        """
        Handle a change in the dock location or state.
        """
        if floating:
            self.setWindowOpacity(0.8)

    def setCurrent(self, item):
        """
        Set the current item.
        """
        self.currentItem = item

    def display(self):
        """
        Show the properties of the current item.
        """
        if not self.currentItem:
            return
        self.removeRows()
        for prop, value in self.currentItem.getProperties().iteritems():
            editable = True
            checkable = False
            combo = False
            enabled = True
            if prop in ["Hub mode", "OVS mode"]:
                checkable = True
            elif prop == "Hosts":
                combo = True
            elif prop == "os":
                combo = True
                value = ["glinux","buster","jessie"]
            elif self.currentItem.device_type in ["Switch", "OVSwitch"]:
                if prop == "subnet" or prop == "mask":
                    continue    # editable = False
            if self.currentItem.device_type == "OVSwitch" or \
                    (self.currentItem.device_type == "Switch" and prop == "OVS mode"):
                enabled = False
            self.addProperty(prop, value, editable, checkable, combo, enabled)

    def clear(self):
        """
        Clear the properties window and release the current item.
        """
        self.currentItem = None
        self.removeRows()

    def removeRows(self):
        """
        Clear the rows of the properties window.
        """
        count = self.model.rowCount()
        if count:
            self.model.removeRows(0, count)


class InterfacesWindow(PropertiesWindow):
    def __init__(self, parent = None):
        """
        Create an interfaces window.
        """
        super(InterfacesWindow, self).__init__(parent)
        self.createView()

        self.currentInterface = 1
        self.leftScroll = QtGui.QPushButton("<")
        self.rightScroll = QtGui.QPushButton(">")
        self.routesButton = QtGui.QPushButton("Routes")

        chooserLayout = QtGui.QHBoxLayout()
        chooserLayout.addWidget(self.leftScroll)
        chooserLayout.addWidget(self.routesButton)
        chooserLayout.addWidget(self.rightScroll)

        mainLayout = QtGui.QVBoxLayout()
        mainLayout.addWidget(self.sourceView)
        mainLayout.addLayout(chooserLayout)

        self.widget = QtGui.QWidget()
        self.widget.setLayout(mainLayout)

        self.setWidget(self.widget)

        self.connect(self.leftScroll, QtCore.SIGNAL("clicked()"), self.scrollLeft)
        self.connect(self.rightScroll, QtCore.SIGNAL("clicked()"), self.scrollRight)

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

    def clear(self):
        """
        Clear the interfaces window and release the current item.
        """
        self.currentItem = None
        self.currentInterface = 1
        self.setWindowTitle("Interfaces")
        self.removeRows()

    def getCurrent(self):
        """
        Return the current item.
        """
        return self.currentItem


class RoutesWindow(InterfacesWindow):
    def __init__(self, interfacesWindow, parent=None):
        """
        Create a routes window.
        """
        super(RoutesWindow, self).__init__(parent)
        self.interfacesWindow = interfacesWindow
        self.currentInterface = 1
        self.currentRoute = 1

        self.connect(self.interfacesWindow.leftScroll, QtCore.SIGNAL("clicked()"), self.decInterface)
        self.connect(self.interfacesWindow.rightScroll, QtCore.SIGNAL("clicked()"), self.incInterface)
        self.connect(self.interfacesWindow.routesButton, QtCore.SIGNAL("clicked()"), self.show)

        self.routesButton.setVisible(False)

    def decInterface(self):
        """
        Handle the interfaces window changing to the previous interface.
        """
        if not self.currentItem:
            return
        from Core.Interfaceable import Interfaceable
        if not isinstance(self.currentItem, Interfaceable):
            return

        if self.currentInterface == 1:
            return

        self.currentRoute = 1
        self.display(-1)

    def incInterface(self):
        """
        Handle the interfaces window changing to the next interface.
        """
        if not self.currentItem:
            return
        from Core.Interfaceable import Interfaceable
        if not isinstance(self.currentItem, Interfaceable):
            return

        if self.currentInterface == len(self.currentItem.getInterfaces()):
            return

        self.currentRoute = 1
        self.display(1)

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

    def clear(self):
        """
        Clear the routes window and release the current item.
        """
        self.currentItem = None
        self.currentInterface = 1
        self.currentRoute = 1
        self.setWindowTitle("Routes")
        self.removeRows()

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
