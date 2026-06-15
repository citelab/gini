from Router import *

"""
This class implements firewall device. Work is in progress
"""


class Firewall(Router):
    device_type = "Firewall"

    def __init__(self):
        super(Firewall, self).__init__()

        self.num_interface = 0
        self.interface = []
        self.con_int = {}
        self.next_interface_number = 0

        # create two interfaces when the router is created
        self.add_interface()
        self.add_interface()

    def add_interface(self):
        pass
