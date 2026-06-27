"""The log window to display messages"""

from PyQt4 import QtGui
from Dockable import *
from Core.globals import defaultOptions


class TextEdit(QtGui.QTextEdit):
    def __init__(self, parent=None):
        """
        Create a custom TextEdit so that the context menu is visible.
        """
        super(TextEdit, self).__init__(parent)

    def contextMenuEvent(self, event):
        """
        Customize the context menu so that it is visible.
        """
        menu = self.createStandardContextMenu()
        menu.setPalette(defaultOptions["palette"])
        menu.exec_(event.globalPos())


class LogWindow(Dockable):
    def __init__(self, title, parent=None):
        """
        Create a log window to append messages to.
        """
        super(LogWindow, self).__init__(title, parent)

        self.parent = parent
        self.log = TextEdit(self)
        self.log.setReadOnly(True)
        self.setWidget(self.log)

    def append(self, text):
        """
        Append text to the log.
        """
        self.log.append(text)

    def copy(self):
        """
        Copy selected text from the log.
        """
        self.log.copy()
