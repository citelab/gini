"""The logical object of a node or edge within a topology"""

from PyQt5 import QtCore

class Device:
    def __init__(self):
        """
        Initialize the device.
        """
        self.properties = {}

    def __str__(self):
        return self.getName()

    def __repr__(self):
        return self.getName()
