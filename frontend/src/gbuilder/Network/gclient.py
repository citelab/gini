from PyQt5 import QtNetwork, QtCore
import os, sys, time
from Core.globals import environ, mainWidgets


class Client(QtCore.QThread):
    def __init__(self, parent=None):
        super(Client, self).__init__()
        self.tcpSocket = QtNetwork.QTcpSocket(parent)
        self.connected = False
        self.leftovers = ""
        self.readlength = 0
        self.connecting = False

        if not parent:
            return
        self.tcpSocket.readyRead.connect(self.read)
        self.tcpSocket.connected.connect(self.setConnected)
        self.tcpSocket.error.connect(self.displayError)

        global client
        client = self

    def connectTo(self, address="localhost", port=9000, user="maheswar"):
        """Connect to the server."""
        if self.connecting:
            return
        
        self.connecting = True
        
        self.username = user  # Store username
        self.tcpSocket.connectToHost(address, port)
        
        if not self.tcpSocket.waitForConnected(1000):
            mainWidgets["log"].append("Failed to connect to server!")
            self.connecting = False
            return False
            
        return True

    def isReady(self):
        return self.tcpSocket.bytesToWrite() == 0

    def isConnected(self):
        """Check if connected to server."""
        return self.connected

    def setConnected(self):
        """Handle successful connection."""
        self.connected = True
        self.connecting = False

    def displayError(self, socketError):
        if self.connecting:
            return

        main = mainWidgets["main"]
        if main.isRunning():
            main.setRecovery(True)
            mainWidgets["log"].append("The connection was lost while a topology was running.\nYou can attempt to re-establish the connection by restarting the server.  You can then press run to resume the previous running topology, or stop to stop it.")
            mainWidgets["canvas"].scene().pauseRefresh()

        if socketError == QtNetwork.QAbstractSocket.RemoteHostClosedError:
            print("Lost connection to server.")
        elif socketError == QtNetwork.QAbstractSocket.HostNotFoundError:
            print("The host was not found. Please check the host name and port settings.")
        elif socketError == QtNetwork.QAbstractSocket.ConnectionRefusedError:
            print("The connection was refused by the peer. Make sure the server is running,")
            print("and check that the host name and port settings are correct.")
        else:
            print(f"The following error occurred: {self.tcpSocket.errorString()}.")

        self.connected = False
        self.terminate()

    def read(self):
        instring = self.waitForMessage(str(self.tcpSocket.readAll(), 'utf-8'))
        if instring:
            self.process(instring)

    def waitForMessage(self, instring):
        instring = self.leftovers + instring

        if not self.readlength and instring.find(" ") == -1:
            self.leftovers = instring
            return
        else:
            if not self.readlength:
                length, buf = instring.split(" ", 1)
                self.readlength = int(length)
            else:
                buf = instring
            if len(buf) < self.readlength:
                self.leftovers = buf
                return
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
        except Exception as inst:
            print(type(inst))
            print(inst.args)
            print("invalid command")
            print(commandType, args)

        self.process(self.waitForMessage(""))

    def send(self, message):
        length = str(len(message))
        self.tcpSocket.writeData((length + " " + message).encode('utf-8'))

    def disconnect(self, *args):
        self.tcpSocket.disconnectFromHost()

    def run(self):
        while not self.isConnected():
            time.sleep(1)
        print("connected!")

        message = input("gclient> ")
        while message != "exit":
            self.process(message)
            message = input("gclient> ")

        self.disconnect()


class Callable:
    def __init__(self, anycallable):
        self.__call__ = anycallable


class Command:
    def __init__(self, args):
        global client
        self.args = args
        self.client = client

    def isolateFilename(self, path):
        return path.split("/")[-1].split("\\")[-1]

    def create(command_type, args):
        return commands[command_type](args)

    create = Callable(create)


class ReceivePathCommand(Command):
    def execute(self):
        print(f"setting remote path to {self.args}")
        environ["remotepath"] = self.args + "/"


class SendFileCommand(Command):
    def execute(self):
        targetDir, path = self.args.split(" ", 1)
        filename = self.isolateFilename(path)
        print(f"sending file {filename}")
        with open(path, "rb") as infile:
            self.client.send("file " + targetDir + "/" + filename + " " + infile.read().decode('utf-8'))


class SendStartCommand(Command):
    def execute(self):
        filename = self.isolateFilename(self.args)
        print(f"sending start {filename}")
        self.client.send("start " + filename)


class SendStopCommand(Command):
    def execute(self):
        print("sending stop")
        self.client.send("stop")


class SendKillCommand(Command):
    def execute(self):
        print(f"killing {self.args}")
        self.client.send("kill " + self.args)


class ReceiveDeviceStatusCommand(Command):
    def execute(self):
        scene = mainWidgets["canvas"].scene()
        tm = mainWidgets["tm"]
        device, pid, status = self.args.split(" ", 2)

        name = device
        item = scene.findItem(name)
        if item is not None:
            item.setStatus(status)

        tm.update(device, pid, status)


class ReceiveRouterStatsCommand(Command):
    def execute(self):
        name, queue, size, rate = self.args.split(" ", 3)
        scene = mainWidgets["canvas"].scene()
        scene.findItem(name).setRouterStats(queue, size, rate)


class ReceiveWiresharkCaptureCommand(Command):
    def execute(self):
        pass


commands = {
    "start": SendStartCommand,
    "stop": SendStopCommand,
    "path": ReceivePathCommand,
    "file": SendFileCommand,
    "status": ReceiveDeviceStatusCommand,
    "kill": SendKillCommand,
    "rstats": ReceiveRouterStatsCommand,
    "wshark": ReceiveWiresharkCaptureCommand
}

client = None

if __name__ == "__main__":
    app = QtCore.QCoreApplication(sys.argv)
    client.connectTo("localhost", 9000)

    text = input("gclient> ")
    while text:
        client.send(text)
        text = input("gclient> ")
