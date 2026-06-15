"""The export window to save as image"""

from PyQt4 import QtCore, QtGui
from Core.globals import options, mainWidgets


class ExportWindow(QtGui.QDialog):
    def __init__(self, parent=None):
        """
        Create an export window to save the current canvas as an image.
        """
        super(ExportWindow, self).__init__(parent)

        self.gridCheckBox = QtGui.QCheckBox(self.tr("Save with grid"))
        self.namesCheckBox = QtGui.QCheckBox(self.tr("Save with names"))
        self.gridCheckBox.setChecked(True)
        self.namesCheckBox.setChecked(True)

        chooseButton = QtGui.QPushButton("Select File")

        layout = QtGui.QVBoxLayout()
        layout.addWidget(self.gridCheckBox)
        layout.addWidget(self.namesCheckBox)
        layout.addWidget(chooseButton)

        self.setLayout(layout)
        self.setWindowModality(QtCore.Qt.ApplicationModal)
        self.resize(200, 150)
        self.setWindowTitle("Export Image")

        self.connect(chooseButton, QtCore.SIGNAL("clicked()"), self.chooseFile)

    def chooseFile(self):
        """
        Pop up a file dialog box to determine a save filename, then save it.
        """
        self.hide()
        filename = QtGui.QFileDialog.getSaveFileName(
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
