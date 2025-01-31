"""The logical object of a node or edge within a topology"""

from Core.Device import Device
from Core.globals import nodeTypes, mainWidgets

# receive thing
realMnumber = 2
alist = {'m1ip': '1', 'm1name': '1', 'm1mac': '1', 'm1port': '1',
         'm2ip': '2', 'm2name': '2', 'm2mac': '2', 'm2port': '2'}

class Item:
    def __init__(self):
        """
        Initialize the item.
        """
        pass

    def getName(self):
        """
        Return the name of the item.
        """
        return str(self.getProperty("Name"))

    def getID(self):
        """
        Return the index number of the item.
        """
        return int(self.getName().split("_")[-1])

    def getProperties(self):
        """
        Return the properties of the item.
        """
        return self.properties

    def getProperty(self, propName):
        """
        Return the specified property of the item.
        """
        return self.properties.get(propName)

    def setProperty(self, prop, value):
        """
        Set the specified property of the item.
        """
        self.properties[prop] = str(value)
