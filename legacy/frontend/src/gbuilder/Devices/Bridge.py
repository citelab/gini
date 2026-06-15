from Core.Device import *


class Bridge(Device):
    device_type = "Bridge"

    def __init__(self):
        super(Bridge, self).__init__()

        self.setProperty("port", "")
        self.setProperty("monitor", "")
        self.setProperty("hub", True)   # use hub mode by default 
