from Core.Interfaceable import *
from PyQt4.QtCore import QPoint


class Cloud(Interfaceable):
    """Frontend Implementation of a PaaS"""
    device_type = "Cloud"

    def __init__(self):
        super(Cloud, self).__init__()

        self.lightPoint = QPoint(0, 0)
