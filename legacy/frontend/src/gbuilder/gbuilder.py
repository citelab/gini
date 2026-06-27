#!/usr/bin/python2

import sys
import os

# Check python version number
# TODO: At the moment Gini works on 2.7, will verify and make it compatible with Python3
if sys.version_info[:2] < (2, 7):
    print("Python version <2.7 is not supported.")
    sys.exit(1)

# Check if PyQt4 is installed
try:
    from PyQt4 import QtCore, QtGui
except ImportError, err:
    print "ImportError: ", err
    raw_input("PyQt4 must be installed.  Press Enter to quit.")
    sys.exit(1)

# Check if we have GINI_HOME set
if "GINI_HOME" not in os.environ:
    raw_input("Environment variable GINI_HOME not set, please set it before running gbuilder!")
    sys.exit(1)

import UI.MainWindow
import Core.globals


def demo(canvas):
    pass


if __name__ == "__main__":
    app = QtGui.QApplication(sys.argv)

    QtCore.qsrand(QtCore.QTime(0, 0, 0).secsTo(QtCore.QTime.currentTime()))
    mainWindow = UI.MainWindow.MainWindow(app)
    mainWindow.setWindowTitle(QtCore.QObject.tr(
        mainWindow,
        "%s %s" % (Core.globals.PROG_NAME, Core.globals.PROG_VERSION))
    )
    mainWindow.setWindowIcon(QtGui.QIcon(os.environ["GINI_SHARE"] + "/gbuilder/images/giniLogo.png"))
    mainWindow.setMinimumSize(640, 480)
    mainWindow.resize(1200, 900)

    sys.exit(app.exec_())
