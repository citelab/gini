#!/usr/bin/python2

from PyQt4 import QtNetwork, QtCore
from TaskManager import *
import os, sys, readline, time, subprocess, signal

version = "1.0.1"
GL_PROG = "gloader"
base_data_dir = os.environ["GINI_HOME"] + "/data"
data_dir = base_data_dir
recovery = False
server = None
shell = None


class Server(QtCore.QObject):
    def __init__(self):
        QtCore.QObject.__init__(self)
        self.tcpServer = QtNetwork.QTcpServer(self)
        self.clientConnection = None
        self.leftovers = ""
        self.readlength = 0
        self.connect(self.tcpServer, QtCore.SIGNAL("newConnection()"), self.newConnection)

        global server
        server = self
        self.tm = TaskManager(self)
        self.timer = QtCore.QTimer()
        self.status = None

        self.connect(self.timer, QtCore.SIGNAL("timeout()"), self.refresh)

    def getLeftovers(self):
        return self.leftovers

    def newConnection(self):
        self.clientConnection = self.tcpServer.nextPendingConnection()
        self.connect(self.clientConnection, QtCore.SIGNAL("disconnected()"), self.stop)
        # self.connect(self.clientConnection, QtCore.SIGNAL("disconnected()"),
        #             self.clientConnection, QtCore.SLOT("deleteLater()"))
        self.connect(self.clientConnection, QtCore.SIGNAL("readyRead()"), self.read)

    def checkAlive(self, processName):
        ps = subprocess.Popen(["/bin/bash", "-c", "ps c -u %s -o cmd=" % os.getenv("USER")], stdout=subprocess.PIPE)
        stdout, stderr = ps.communicate()
        lines = str(stdout).split("\n")
        return lines.count(processName)

    def start(self, ip, port):
        if self.checkAlive("gserver") > 1:
            raw_input("Another instance of gserver is already running!")
            sys.exit(1)
        if not self.tcpServer.listen(ip, port):
            raw_input("Unable to start the server: %s." % str(self.tcpServer.errorString()))
            sys.exit(1)
        print "The server is running on port %d." % self.tcpServer.serverPort()

    def getTM(self):
        return self.tm

    def read(self):
        instring = self.waitForMessage(str(self.clientConnection.readAll()))
        if instring:
            self.process(instring)

    def waitForMessage(self, instring):
        instring = self.leftovers + instring

        if not self.readlength and instring.find(" ") == -1:
            self.leftovers = instring
            return ""
        else:
            if not self.readlength:
                length, buf = instring.split(" ", 1)
                self.readlength = int(length)
            else:
                buf = instring
            if len(buf) < self.readlength:
                self.leftovers = buf
                return ""
            else:
                self.leftovers = buf[self.readlength:]
                instring = buf[:self.readlength]
                self.readlength = 0

        return instring

    def process(self, instring):
        if not instring:
            return

        args = ""
        instring = str(instring)

        index = instring.find(" ")
        if index != -1:
            commandType, args = instring.split(" ", 1)
        else:
            commandType = instring

        try:
            command = Command.create(commandType, args)
            command.execute()
        except Exception, inst:
            print type(inst)
            print inst.args
            print inst
            print "invalid command"

        self.process(self.waitForMessage(""))

    def send(self, message):
        if not self.clientConnection:
            print "not connected"
            return
        length = str(len(message))
        self.clientConnection.write(length + " " + message)

    def processStatus(self, devices):
        self.status = devices

    def getStatus(self):
        return self.status

    def getTimer(self):
        return self.timer

    def refresh(self):
        if not self.clientConnection:
            return

        statusList = []
        deviceList = []
        for device, (pid, status) in self.status.iteritems():
            Command.create("status", device + " " + pid + " " + status.lower()).execute()
            statusList.append(status)
            deviceList.append((device, pid))

        for status in statusList:
            if status == "":
                self.timer.stop()
                return

        for status in statusList:
            if status != "dead":
                return

        if statusList:
            self.tm.stop()

        for device, pid in deviceList:
            self.status[device] = (pid, "")

    def stop(self):
        self.clientConnection.disconnectFromHost()
        sys.exit(0)


"""
class ShellStarter(QtCore.QThread):
    def __init__(self, command):
        QtCore.QThread.__init__(self)
        self.command = str(command)
        self.started = -1

    def startStatus(self):
        return self.started

    def run(self):
        self.started = 0
        os.system(self.command)
        self.started = 1
"""


class Callable:
    def __init__(self, anycallable):
        self.__call__ = anycallable


class Command:
    mutex = QtCore.QMutex()

    def __init__(self, args):
        global server
        self.args = args
        self.server = server

    @classmethod
    def waitOne(cls):
        cls.mutex.lock()

    @classmethod
    def release(cls):
        cls.mutex.unlock()

    def create(commandType, args):
        return commands[commandType](args)
    create = Callable(create)


class ReceiveInitStartCommand(Command):
    def execute(self):
        print "cleaning and checking directories..."
        global data_dir
        data_dir = base_data_dir + "/" + self.args

        if self.server.checkAlive("glinux") or \
                self.server.checkAlive("uswitch") or \
                self.server.checkAlive("grouter") or \
                self.server.checkAlive("gpox"):
            print "A previous running topology was not stopped properly."
            global recovery
            recovery = True
            return

        # clear old mach_mconsole files
        subprocess.call(["/bin/bash", "-c", "rm -rf ./.mach/*"])

        # make data dir if necessary
        if not os.access(data_dir, os.F_OK):
            os.mkdir(data_dir, 0755)

        subprocess.call(["/bin/bash", "-c", "rm -rf %s/Router_* %s/Switch_*" % (data_dir, data_dir)])

        # make mobile_data dir if necessary
        mobile_dir = data_dir + "/mobile_data"
        if not os.access(mobile_dir, os.F_OK):
            os.mkdir(mobile_dir, 0755)


class ReceiveCanvasInfoCommand(Command):
    def execute(self):
        # print "writing canvas info..."
        # mobile_dir = data_dir + "/mobile_data"
        #
        # canvasOut = open(mobile_dir + "/canvas.data", "w")
        # canvasOut.write(self.args)
        # canvasOut.close()
        pass


class ReceiveStartCommand(Command):
    def execute(self):
        self.server.getTM().start()
        self.server.getTimer().start(1000)

        global recovery
        if recovery:
            recovery = False
            return

        command = GL_PROG + " -c " + os.environ["GINI_HOME"] + "/" + self.args + " -s %s -r %s -u %s -o %s -C %s" % (data_dir, data_dir, data_dir, data_dir, data_dir)

        subprocess.Popen(["/bin/bash", "-c", command])


class ReceiveStopCommand(Command):
    def execute(self):
        print "stopping..."
        command = "gloader -d"

        subprocess.Popen(["/bin/bash", "-c", command])


class ReceiveFileCommand(Command):
    def execute(self):
        print "receiving file..."
        filename, body = self.args.split(" ", 1)
        outfile = open(os.environ["GINI_HOME"] + "/" + filename, "w")
        outfile.write(body)
        outfile.close()


class SendDeviceStatusCommand(Command):
    def execute(self):
        self.server.send("status " + self.args)


class ShowDevicesStatusCommand(Command):
    def execute(self):
        for key, val in self.server.getStatus().iteritems():
            print key, val


class ReceiveKillCommand(Command):
    def execute(self):
        subprocess.call(["/bin/bash", "-c", "kill " + self.args])


class ListCommand(Command):
    def execute(self):
        subprocess.call(["/bin/bash", "-c", "screen -ls"])
        subprocess.call(["/bin/bash", "-c", "ps auxw | grep uswitch"])


class KillallCommand(Command):
    def execute(self):
        subprocess.call(["/bin/bash", "-c", "killall glinux grouter uswitch gwcenter"])


class LeftoversCommand(Command):
    def execute(self):
        print self.server.getLeftovers()


class FlushBufferCommand(Command):
    def execute(self):
        print "flushing"
        for (pid, device), status in self.server.getStatus().iteritems():
            Command.create("status", device + " " + status).execute()


class ScreenCommand(Command):
    def execute(self):
        print "executing screen command"
        subprocess.call(["/bin/bash", "-c", "screen " + self.args])


class ReceiveRequestRouterStatsCommand(Command):
    def execute(self):
        # print "sending stats for " + self.args + "..."
        index = self.args.split("_")[-1]

        old_dir = os.getcwd()
        os.chdir(data_dir + "/%s" % self.args)

        filename = "%s.out" % self.args
        if not os.access(filename, os.F_OK):
            subprocess.call(["/bin/bash", "-c", "cat %s.info > %s &" % (self.args, filename)])

        try:
            infoIn = open(filename, "r")
            lines = infoIn.readlines()
            infoIn.close()
        except:
            os.chdir(old_dir)
            return

        for line in lines:
            if line.find("//") == 0 or line.find("\x00") == 0:
                continue
            try:
                (date,queue,size,rate) = line.strip().split("\t")
                self.server.send("rstats " + self.args + " " + queue + " " + size + " " + rate)
            except:
                print "exception caught"
                print line

        open(filename, "w").close()
        os.chdir(old_dir)


class ReceiveRequestWiresharkCaptureCommand(Command):
    def execute(self):
        # print "sending capture for " + self.args + "..."
        global shell
        shell = CaptureSender(self.args)
        shell.start()


class ReceiveRestartCommand(Command):
    def execute(self):
        print "restarting " + self.args + "..."
        if self.args.find("Router") == 0:
            self.restartRouter()
        elif self.args.find("Mach") == 0 or self.args.find("Mobile") == 0:
            self.restartMach()
        elif self.args.find("REALM") == 0:
            self.restartMach()
        elif self.args.find("Switch") == 0:
            self.restartSwitch()

    def restartRouter(self):
        router_dir = data_dir + "/" + self.args
        old_dir = os.getcwd()
        os.chdir(router_dir)
        if os.access("grouter.conf", os.F_OK) and os.access("startit.sh", os.F_OK):
            if os.access(self.args + ".pid", os.F_OK):
                try:
                    pidIn = open(self.args + ".pid", "r")
                    pid = pidIn.readline().strip()
                    pidIn.close()
                    os.kill(int(pid), signal.SIGTERM)
                    time.sleep(0.5)
                except:
                    pass
            subprocess.call(["/bin/bash", "-c", "./startit.sh"])
        else:
            print "failed to restart " + self.args
        os.chdir(old_dir)

    def restartMach(self):
        subprocess.call(["/bin/bash", "-c", "cp %s/tmp/Mach_bak/FE* %s/tmp" % (os.environ["GINI_HOME"], os.environ["GINI_HOME"])])
        if subprocess.call(["/bin/bash", "-c", "mach_mconsole " + self.args + " reboot"], stdout=open("/dev/null", "w"), stderr=open("/dev/null", "w")):
            mach_dir = data_dir + "/" + self.args
            old_dir = os.getcwd() or self.args.find("Mobile") == 0 or self.args.find("REALM") == 0
            os.chdir(mach_dir)
            subprocess.call(["/bin/bash", "-c", "./startit.sh"])
            os.chdir(old_dir)

    def restartSwitch(self):
        switch_dir = data_dir + "/" + self.args
        old_dir = os.getcwd()
        os.chdir(switch_dir)
        if os.access("uswitch.pid", os.F_OK):
            try:
                pidIn = open("uswitch.pid", "r")
                pid = pidIn.readline().strip()
                pidIn.close()
                os.kill(int(pid), signal.SIGTERM)
            except:
                pass
        if os.access("gini_socket.ctl", os.F_OK):
            try:
                os.remove("gini_socket.ctl")
            except:
                pass
        if os.access("startit.sh", os.F_OK):
            subprocess.Popen(["/bin/bash", "-c", "./startit.sh"])
        else:
            print "failed to restart " + self.args
        os.chdir(old_dir)


class ReceiveTerminateCommand(Command):
    def execute(self):
        print "terminating " + self.args + "..."
        if self.args.find("Router") == 0:
            self.terminateRouter()
        elif self.args.find("Mach") == 0 or self.args.find("Mobile") == 0 or self.args.find("REALM") == 0:
            self.terminateMach()

    def terminateMach(self):
        subprocess.call(["/bin/bash", "-c", "mach_mconsole " + self.args + " cad"], stdout=open("/dev/null", "w"))

    def terminateRouter(self):
        router_dir = data_dir + "/" + self.args
        pidIn = open(router_dir + "/" + self.args + ".pid", "r")
        pid = pidIn.readline().strip()
        pidIn.close()
        os.kill(int(pid), signal.SIGTERM)


class ShowStatusCommand(Command):
    def execute(self):
        print self.server.getTM().getStatus()


class CaptureSender(QtCore.QThread):
    def __init__(self, router_name):
        QtCore.QThread.__init__(self)
        self.router_name = router_name

    def run(self):
        command = ["cat", data_dir + "/" + self.router_name + "/" + self.router_name + ".port"]
        pobj = subprocess.Popen(command, stdout=subprocess.PIPE)
        buf = "a"
        while buf:
            buf = pobj.stdout.read(1024)
            server.send("wshark " + self.router_name + " " + buf)


commands = {
    "leftovers": LeftoversCommand,
    "list": ListCommand,
    "kill": ReceiveKillCommand,
    "killall": KillallCommand,
    "start": ReceiveStartCommand,
    "stop": ReceiveStopCommand,
    "file": ReceiveFileCommand,
    "status": SendDeviceStatusCommand,
    "statusall": ShowDevicesStatusCommand,
    "flush": FlushBufferCommand,
    "screen": ScreenCommand,
    "init": ReceiveInitStartCommand,
    "canvas": ReceiveCanvasInfoCommand,
    "rstats": ReceiveRequestRouterStatsCommand,
    "wshark": ReceiveRequestWiresharkCaptureCommand,
    "restart": ReceiveRestartCommand,
    "terminate": ReceiveTerminateCommand,
    "tm": ShowStatusCommand
}

if __name__ == "__main__":
    app = QtCore.QCoreApplication(sys.argv)
    if len(sys.argv) > 1:
        port = int(sys.argv[1])
    else:
        port = 10000

    server = Server()
    server.start(QtNetwork.QHostAddress("127.0.0.1"), port)

    text = raw_input("gserver " + version + "> ")
    while text != "exit":
        server.process(text)
        text = raw_input("gserver " + version + "> ")
