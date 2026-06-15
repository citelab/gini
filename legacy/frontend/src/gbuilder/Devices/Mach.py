from Core.Interfaceable import *
from PyQt4.QtCore import QPoint


class Mach(Interfaceable):
    device_type = "Mach"

    def __init__(self):
        super(Mach, self).__init__()

        self.setProperty("os", "glinux")

        self.setProperty("ipv4", "")
        self.setProperty("port", "")
        self.setProperty("mac", "")

        self.lightPoint = QPoint(-10, 3)
