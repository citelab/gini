"""The window to specify which directory to send a file to"""

from PyQt5 import QtCore, QtGui, QtWidgets
from Core.globals import options, mainWidgets


class SendDirectoryWindow(QtWidgets.QDialog):
    def __init__(self, parent=None):
        """
        Create a send directory window to send a file to the server.
        """
        super(SendDirectoryWindow, self).__init__(parent)

        self.filename = ""
        self.radio1 = QtWidgets.QRadioButton("bin")
        self.radio2 = QtWidgets.QRadioButton("tmp")
        self.radio3 = QtWidgets.QRadioButton("data")
        self.filenameLabel = QtWidgets.QLabel("")
        self.sendButton = QtWidgets.QPushButton("Send")
        self.cancelButton = QtWidgets.QPushButton("Cancel")
        self.choices = [self.radio1, self.radio2, self.radio3]

        buttonLayout = QtWidgets.QHBoxLayout()
        buttonLayout.addWidget(self.sendButton)

        layout = QtWidgets.QVBoxLayout()
        layout.addWidget(self.filenameLabel)
        layout.addWidget(self.radio1)
        layout.addWidget(self.radio2)
        layout.addWidget(self.radio3)
        layout.addLayout(buttonLayout)

        self.setLayout(layout)
        self.setWindowModality(QtCore.Qt.ApplicationModal)
        self.resize(250, 150)
        self.setWindowTitle("Destination Directory")

        self.sendButton.clicked.connect(self.send)
        self.cancelButton.clicked.connect(self.reject)

    def setFilename(self, filename):
        """
        Set the filename to send to the server.
        """
        self.filename = filename
        self.filenameLabel.setText(filename)

    def send(self):
        """
        Send the file to the server.
        """
        self.hide()
        client = mainWidgets["client"]
        if not client:
            return

        for radio in self.choices:
            if radio.isChecked():
                client.process("file " + radio.text() + " " + self.filename)
                return
