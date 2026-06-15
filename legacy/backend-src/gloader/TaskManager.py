from PyQt4 import QtCore
import os
import time

devFilter = ["Mach_", "Router_", "OpenFlowController_", "OVSwitch_", "Cloud_"]


class TaskManager(QtCore.QThread):
    def __init__(self, server=None):
        QtCore.QThread.__init__(self)
        self.server = server
        self.running = False

    def reportStatus(self):
        devices = self.devices.copy()
        self.server.processStatus(devices)

    def stop(self):
        self.running = False

    def getStatus(self):
        return self.devices

    def isRunning(self):
        return self.running

    def run(self):
        self.running = True
        self.devices = {}
        while self.running:
            os.system("screen -wipe > %s/tmp/screenPID" % os.environ["GINI_HOME"])
            screenIn = open("%s/tmp/screenPID" % os.environ["GINI_HOME"], "r")
            lines = screenIn.readlines()
            screenIn.close()

            devices = {}
            for device, (pid, status) in self.devices.iteritems():
                devices[device] = (pid, "dead")

            for i in range(1, len(lines)-1):
                line = lines[i].split("\t")
                parts = line[1].split(".")
                status = line[-1].strip("()\n").split(", ")[-1]
                for dev in devFilter:
                    if parts[1].find(dev) == 0:
                        devices[parts[1]] = (parts[0], status)
            self.devices = devices
            self.reportStatus()
            time.sleep(1)

        os.system("rm %s/tmp/screenPID" % os.environ["GINI_HOME"])


if __name__ == "__main__":
    TaskManager().manage()
