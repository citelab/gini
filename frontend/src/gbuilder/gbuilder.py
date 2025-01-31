#!/usr/bin/env python3

import sys
import os

# Add the gbuilder directory to Python path
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

# Check python version number
if sys.version_info[:2] < (3, 6):
    print("Python version <3.6 is not supported.")
    sys.exit(1)

# Check if PyQt5 is installed
try:
    from PyQt5 import QtCore, QtGui, QtWidgets
except ImportError as err:
    print(f"ImportError: {err}")
    input("PyQt5 must be installed. Press Enter to quit.")
    sys.exit(1)

# Check if we have GINI_HOME set
if "GINI_HOME" not in os.environ:
    input("Environment variable GINI_HOME not set, please set it before running gbuilder!")
    sys.exit(1)

# Use local imports since we added current_dir to path
from UI.MainWindow import MainWindow
import Core.globals as globals
from Network.gclient import Client


def demo(canvas):
    pass


def main():
    app = QtWidgets.QApplication(sys.argv)
    
    QtCore.qsrand(QtCore.QTime(0, 0, 0).secsTo(QtCore.QTime.currentTime()))
    
    # Initialize mainWidgets dictionary
    globals.mainWidgets = {}
    
    # Create and show main window first
    mainWindow = MainWindow(app)
    mainWindow.setWindowTitle(
        "%s %s" % (globals.PROG_NAME, globals.PROG_VERSION)
    )
    mainWindow.setWindowIcon(QtGui.QIcon(os.environ["GINI_SHARE"] + "/gbuilder/images/giniLogo.png"))
    mainWindow.setMinimumSize(640, 480)
    mainWindow.resize(1200, 900)
    mainWindow.show()

    # Then try to connect to gserver
    client = Client(None)
    globals.mainWidgets["client"] = client
    
    # Try to connect but don't exit on failure
    if not client.connectTo(address="localhost", port=9000, user="maheswar"):
        print("Failed to connect to gserver")
        # Don't exit, just show warning in log window
        if "log" in globals.mainWidgets:
            globals.mainWidgets["log"].append("Failed to connect to gserver. Please start gserver and try again.")

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
