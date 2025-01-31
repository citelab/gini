"""The export window to save as image"""

from PyQt5 import QtCore, QtGui, QtWidgets
from Core.globals import options, mainWidgets


class ExportWindow(QtWidgets.QDialog):
    def __init__(self, parent=None):
        """
        Create an export window to save the current canvas as an image.
        """
        super(ExportWindow, self).__init__(parent)

        self.gridCheckBox = QtWidgets.QCheckBox(self.tr("Save with grid"))
        self.namesCheckBox = QtWidgets.QCheckBox(self.tr("Save with names"))
        self.gridCheckBox.setChecked(True)
        self.namesCheckBox.setChecked(True)

        chooseButton = QtWidgets.QPushButton("Select File")

        layout = QtWidgets.QVBoxLayout()
        layout.addWidget(self.gridCheckBox)
        layout.addWidget(self.namesCheckBox)
        layout.addWidget(chooseButton)

        self.setLayout(layout)
        self.setWindowModality(QtCore.Qt.ApplicationModal)
        self.resize(200, 150)
        self.setWindowTitle("Export Image")

        chooseButton.clicked.connect(self.chooseFile)

    def chooseFile(self):
        """
        Pop up a file dialog box to determine a save filename, then save it.
        """
        self.hide()
        filename = QtWidgets.QFileDialog.getSaveFileName(
            self,
            self.tr("Choose a file name"), ".",
            self.tr("PNG (*.png)")
        )
        if filename.isEmpty():
            return

        if not filename.toLower().endsWith(".png"):
            filename += ".png"

        canvas = mainWidgets["canvas"]
        sceneRect = canvas.sceneRect()
        viewRect = canvas.mapFromScene(sceneRect).boundingRect()

        image = QtGui.QImage(viewRect.width(), viewRect.height(), QtGui.QImage.Format_ARGB32)
        painter = QtGui.QPainter(image)

        oldGridOption = options["grid"]
        oldNamesOption = options["names"]
        options["grid"] = self.gridCheckBox.isChecked()
        options["names"] = self.namesCheckBox.isChecked()
        canvas.render(painter, QtCore.QRectF(), viewRect)
        options["grid"] = oldGridOption
        options["names"] = oldNamesOption

        painter.end()

        image.save(filename)

        self.parent().statusBar().showMessage(self.tr("Ready"), 2000)
