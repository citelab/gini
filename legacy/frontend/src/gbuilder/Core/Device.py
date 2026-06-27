"""The logical object of a node"""

from UI.Node import *


class Device(Node):
    def __init__(self):
        """
        Create a logical side to the node.
        """
        super(Device, self).__init__()
        self.connection = []

    def __str__(self):
        return self.getName()

    def __repr__(self):
        return self.getName()
