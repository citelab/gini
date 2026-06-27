"""A device that can be attached to"""

from Device import *
from Core.globals import options
import subprocess
import os
import time


class Attachable(Device):
    def __init__(self):
        """
        Create a device that can be attached to.
        """
        super(Attachable, self).__init__()

        self.menu.addAction("Restart", self.restart)
        self.menu.addAction("Stop", self.terminate)
        self.shell = None

    def attach(self):
        """
        Attach to corresponding device on backend.
        """
        name = self.getName()
        command = ""

        window_name = str(self.getProperty("Name"))  # the string cast is necessary for cloning
        if self.getName() != window_name:
            window_name += " (" + self.getName() + ")"

        print("Attaching to device: %s" % name)
        if options["gnome"] == True:
            command += "gnome-terminal -fa 'Monospace' -fs 14 -title \"" + window_name + "\" -e  screen -r " + window_name
        else:
            command += "xterm -fa 'Monospace' -fs 14 -title \"" + window_name + "\" -e  screen -r " + window_name

        self.shell = subprocess.Popen(str(command), shell=True)

        # enhancement: if this is a Router, then create a screen pane at the bottom and run tshark on it
        if window_name[0:6] == 'Router':
            # allow the previous command to finish running
            time.sleep(1)

            # commands for splitting screen window, resizing, switching focus, running a shell
            cmd_split = "screen -S $(screen -ls | awk '{print $1}' | grep %s) -X split" % (window_name)
            cmd_resize = "screen -S $(screen -ls | awk '{print $1}' | grep %s) -X resize +20%%" % (window_name)
            cmd_switch_focus = "screen -S $(screen -ls | awk '{print $1}' | grep %s) -X focus" % (window_name)
            cmd_screen = "screen -S $(screen -ls | awk '{print $1}' | grep %s) -X screen" % (window_name)

            # look at config file for interfaces names e.g. tap1, tap2
            cmd_tshark = "screen -S $(screen -ls | awk '{print $1}' | grep %s) -X stuff 'tshark" % (window_name)
            with open(os.environ["GINI_HOME"] + "/data/" + window_name + "/grouter.conf") as f:
                lines = f.readlines()
            for line in lines:
                words = line.split()
                if words[0] == 'ifconfig' and words[1] == 'add':
                    cmd_tshark += " -i " + words[2]
            cmd_tshark += " ^M'"

            # execute commands
            #p = subprocess.Popen(cmd_split, stdout=subprocess.PIPE, shell=True)
            #p = subprocess.Popen(cmd_resize, stdout=subprocess.PIPE, shell=True)
            #p = subprocess.Popen(cmd_switch_focus, stdout=subprocess.PIPE, shell=True)
            #p = subprocess.Popen(cmd_screen, stdout=subprocess.PIPE, shell=True)
            #p = subprocess.Popen(cmd_tshark, stdout=subprocess.PIPE, shell=True)
            #p = subprocess.Popen(cmd_switch_focus, stdout=subprocess.PIPE, shell=True)
