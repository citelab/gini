from Core.Device import *

"""
This class implements a hub device. Work is in progress
"""


class Hub(Device):
    device_type="Hub"

    def __init__(self):
        super(Hub, self).__init__()

        self.setProperty("port", "")
        self.setProperty("monitor", "")
        self.setProperty("hub", True)   # use hub mode by default
