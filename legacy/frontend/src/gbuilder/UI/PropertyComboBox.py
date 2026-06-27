from PyQt4 import QtCore, QtGui


class PropertyComboBox(QtGui.QComboBox):

    def __init__(self, item, PropertiesWindow, prop, values, parent=None):
        super(PropertyComboBox, self).__init__(parent)

        self.item = item
        self.prop = prop
        self.PropertiesWindow = PropertiesWindow

        self.addItems(values)

        if values:
            if hasattr(self.item, 'hostIndex'):
                self.setCurrentIndex(self.item.hostIndex)
                self.connect(self, QtCore.SIGNAL("currentIndexChanged(int)"), self.changeIndex)
        self.currentIndexChanged.connect(self.comboBoxChanged)

        item.setProperty(prop, self.item.properties[prop])




    def changeIndex(self, index):
        self.item.hostIndex = index
        text = self.currentText()

        real_ip = self.item.hostsproperty[str(text)].ip
        self.item.setProperty("realmachineIPv4", real_ip)

        real_mac = self.item.hostsproperty[str(text)].mac
        self.item.setProperty("realmachineMac", real_mac)

        real_port = self.item.hostsproperty[str(text)].port
        self.item.setProperty("realmachinePort", real_port)
        self.PropertiesWindow.display()


    def comboBoxChanged(self):

        self.item.setProperty(self.prop, self.currentText())
