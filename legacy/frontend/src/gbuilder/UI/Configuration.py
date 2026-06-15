"""The configuration window and options"""

import sys, os, random
from PyQt4 import QtCore, QtGui
from Core.globals import *
from Core.utils import ip_utils


class SaveConfigurationError(Exception):
    pass


class LineEdit(QtGui.QLineEdit):
    def __init__(self, text=QtCore.QString()):
        """
        Create a custom LineEdit so that the context menu is visible.
        """
        super(LineEdit, self).__init__(text)

    def contextMenuEvent(self, event):
        """
        Customize the context menu so that it is visible.
        """
        menu = self.createStandardContextMenu()
        menu.setPalette(defaultOptions["palette"])
        menu.exec_(event.globalPos())


class ServerPage(QtGui.QWidget):
    def __init__(self, parent=None):
        """
        Create a server configuration page.
        """
        super(ServerPage, self).__init__(parent)

        configGroup = QtGui.QGroupBox(self.tr("Server configuration"))

        self.autoconnectCheckBox = QtGui.QCheckBox("Automatically start server on gbuilder startup")
        self.autoconnectCheckBox.setChecked(True)

        usernameLabel = QtGui.QLabel(self.tr("Username:"))
        self.usernameLine = LineEdit()

        usernameLayout = QtGui.QHBoxLayout()
        usernameLayout.addWidget(usernameLabel)
        usernameLayout.addWidget(self.usernameLine)

        serverLabel = QtGui.QLabel(self.tr("Server:"))
        self.serverCombo = QtGui.QComboBox()
        self.serversFilename = environ["config"]+"servers"

        try:
            if not os.access(self.serversFilename, os.F_OK):
                open(self.serversFilename, "w").close()

            infile = open(self.serversFilename, "r")
            servers = infile.readlines()
            infile.close()

            for server in servers:
                self.serverCombo.addItem(self.tr(server.strip()))

        except:
            mainWidgets["log"].append("Failed to read from server list!")

        self.serverLine = LineEdit()
        self.addServerButton = QtGui.QPushButton("Add")
        self.delServerButton = QtGui.QPushButton("Delete")

        serverLayout = QtGui.QGridLayout()
        serverLayout.addWidget(serverLabel, 0, 0)
        serverLayout.addWidget(self.serverCombo, 0, 1)
        serverLayout.addWidget(self.serverLine, 1, 1)
        serverLayout.addWidget(self.delServerButton, 0, 2)
        serverLayout.addWidget(self.addServerButton, 1, 2)

        sessionLabel = QtGui.QLabel(self.tr("Session Name (if using Putty):"))
        self.sessionLine = LineEdit()

        sessionLayout = QtGui.QHBoxLayout()
        sessionLayout.addWidget(sessionLabel)
        sessionLayout.addWidget(self.sessionLine)

        tunnelGroup = QtGui.QGroupBox(self.tr("SSH Tunnel Port Configuration"))

        localPortLabel = QtGui.QLabel(self.tr("Local Port:"))
        remotePortLabel = QtGui.QLabel(self.tr("Remote Port:"))
        self.localPortLine = LineEdit()
        self.remotePortLine = LineEdit()
        self.localPortButton = QtGui.QPushButton("Randomize")
        self.remotePortButton = QtGui.QPushButton("Randomize")

        portLayout = QtGui.QGridLayout()
        portLayout.addWidget(localPortLabel, 0, 0)
        portLayout.addWidget(self.localPortLine, 0, 1)
        portLayout.addWidget(self.localPortButton, 0, 2)
        portLayout.addWidget(remotePortLabel, 1, 0)
        portLayout.addWidget(self.remotePortLine, 1, 1)
        portLayout.addWidget(self.remotePortButton, 1, 2)

        configLayout = QtGui.QVBoxLayout()
        configLayout.addWidget(self.autoconnectCheckBox)
        configLayout.addLayout(usernameLayout)
        configLayout.addLayout(serverLayout)
        configLayout.addLayout(sessionLayout)

        configGroup.setLayout(configLayout)
        tunnelGroup.setLayout(portLayout)

        mainLayout = QtGui.QVBoxLayout()
        mainLayout.addWidget(configGroup)
        mainLayout.addWidget(tunnelGroup)
        mainLayout.addStretch(1)

        self.setLayout(mainLayout)
        self.connect(self.delServerButton, QtCore.SIGNAL("clicked()"),
                     self.delServer)
        self.connect(self.addServerButton, QtCore.SIGNAL("clicked()"), self.addServer)
        self.connect(self.localPortButton, QtCore.SIGNAL("clicked()"), self.randomizeLocalPort)
        self.connect(self.remotePortButton, QtCore.SIGNAL("clicked()"), self.randomizeRemotePort)

        self.updateSettings()

    def addServer(self):
        """
        Add a server to the list and write it to file.
        """
        text = self.serverLine.text().trimmed()
        if text:
            index = self.serverCombo.findText(text)
            if index == -1:
                # text not already in server list
                self.serverCombo.addItem(self.tr(text))
                index = self.serverCombo.findText(self.tr(text))
                try:
                    outfile = open(environ["config"] + "servers", "a")
                    outfile.write(str(text)+"\n")
                    outfile.close()
                except:
                    mainWidgets["log"].append("Failed to write to server list!")
            self.serverCombo.setCurrentIndex(index)
            self.serverLine.clear()

    def delServer(self):
        """
        Delete a server from the list.
        """
        try:
            file = open(self.serversFilename, "w+")
        except Exception, err:
            mainWidgets["log"].append("Failed to open server list: " + str(err))
        else:
            try:
                lines = file.readlines()
                file.seek(0)
                curLine = self.serverCombo.currentText()
                for line in lines:
                    line = line.strip()
                    if line == curLine:
                        continue
                    file.write(line)
            except Exception, err:
                mainWidgets["log"].append(
                        "Failed to delete server: " + str(err))
            else:
                self.serverCombo.removeItem(self.serverCombo.currentIndex())
            file.close()

    def randomizeLocalPort(self):
        """
        Randomize local port field.
        """
        port = str(random.randint(1024, 65535))
        self.localPortLine.setText(port)

    def randomizeRemotePort(self):
        """
        Randomize remote port field.
        """
        port = str(random.randint(1024, 65535))
        self.remotePortLine.setText(port)

    def saveOptions(self):
        """
        Save options handled by this page.
        """
        options["autoconnect"] = self.autoconnectCheckBox.isChecked()
        options["username"] = self.usernameLine.text()
        options["server"] = self.serverCombo.currentText()
        options["session"] = self.sessionLine.text()
        options["localPort"] = self.localPortLine.text()
        options["remotePort"] = self.remotePortLine.text()

    def updateSettings(self):
        """
        Update the page with current options.
        """
        self.autoconnectCheckBox.setChecked(options["autoconnect"])
        self.usernameLine.setText(options["username"])
        index = self.serverCombo.findText(options["server"])
        if index == -1 and options["server"]:
            self.serverLine.setText(options["server"])
            self.addServer()
        else:
            self.serverCombo.setCurrentIndex(index)
        self.sessionLine.setText(options["session"])
        self.localPortLine.setText(options["localPort"])
        self.remotePortLine.setText(options["remotePort"])


class GeneralPage(QtGui.QWidget):
    def __init__(self, parent=None):
        """
        Create a general configuration page.
        """
        super(GeneralPage, self).__init__(parent)

        uiGroup = QtGui.QGroupBox(self.tr("User Interface"))
        self.createUICheckboxes()
        self.createBrowsables()

        gridLayout = QtGui.QHBoxLayout()
        gridLayout.addWidget(self.gridLine)
        gridLayout.addWidget(self.chooseGridColorButton)
        gridLayout.setAlignment(QtCore.Qt.AlignLeft)

        backgroundLayout = QtGui.QHBoxLayout()
        backgroundLayout.addWidget(self.backgroundLine)
        backgroundLayout.addWidget(self.browseBackgroundButton)
        backgroundLayout.addWidget(self.chooseBackgroundButton)
        backgroundLayout.setAlignment(QtCore.Qt.AlignLeft)

        windowThemeLayout = QtGui.QHBoxLayout()
        windowThemeLayout.addWidget(self.windowThemeLine)
        windowThemeLayout.addWidget(self.browseWindowThemeButton)
        windowThemeLayout.addWidget(self.chooseWindowColorButton)
        windowThemeLayout.setAlignment(QtCore.Qt.AlignLeft)

        baseThemeLayout = QtGui.QHBoxLayout()
        baseThemeLayout.addWidget(self.baseThemeLine)
        baseThemeLayout.addWidget(self.browseBaseThemeButton)
        baseThemeLayout.addWidget(self.chooseBaseColorButton)
        baseThemeLayout.setAlignment(QtCore.Qt.AlignLeft)

        # TODO: Add configuration option for item spacing here
        # self.item_spacing_slider = QtGui.QSlider(QtCore.Qt.Horizontal)

        uiLayout = QtGui.QVBoxLayout()
        uiLayout.addWidget(self.namesCheckBox)
        uiLayout.addWidget(self.gridCheckBox)
        uiLayout.addWidget(self.smoothingCheckBox)
        uiLayout.addWidget(self.systrayCheckBox)
        uiLayout.addWidget(self.restoreLayoutCheckBox)
        uiLayout.addWidget(self.moveAlertCheckBox)
        uiLayout.addWidget(self.gnomeTerminalCheckBox)
        uiLayout.addWidget(self.menuModsCheckBox)

        uiLayout.addWidget(QtGui.QLabel(self.tr("Grid Color: ")))
        uiLayout.addLayout(gridLayout)
        uiLayout.addWidget(QtGui.QLabel(self.tr("Background: ")))
        uiLayout.addLayout(backgroundLayout)
        uiLayout.addWidget(QtGui.QLabel(self.tr("Window Theme: ")))
        uiLayout.addLayout(windowThemeLayout)
        uiLayout.addWidget(QtGui.QLabel(self.tr("Base Theme: ")))
        uiLayout.addLayout(baseThemeLayout)
        uiGroup.setLayout(uiLayout)

        mainLayout = QtGui.QVBoxLayout()
        mainLayout.addWidget(uiGroup)
        mainLayout.addSpacing(12)
        mainLayout.addStretch(1)

        self.setLayout(mainLayout)
        self.updateSettings()
        self.updateLook()

        self.connect(self.browseBackgroundButton, QtCore.SIGNAL("clicked()"), self.browseBackground)
        self.connect(self.browseWindowThemeButton, QtCore.SIGNAL("clicked()"), self.browseWindowTheme)
        self.connect(self.browseBaseThemeButton, QtCore.SIGNAL("clicked()"), self.browseBaseTheme)
        self.connect(self.chooseGridColorButton, QtCore.SIGNAL("clicked()"), self.chooseGridColor)
        self.connect(self.chooseBackgroundButton, QtCore.SIGNAL("clicked()"), self.chooseBackgroundColor)
        self.connect(self.chooseWindowColorButton, QtCore.SIGNAL("clicked()"), self.chooseWindowColor)
        self.connect(self.chooseBaseColorButton, QtCore.SIGNAL("clicked()"), self.chooseBaseColor)

    def createUICheckboxes(self):
        """
        Create checkboxes for UI options.
        """
        self.namesCheckBox = QtGui.QCheckBox(self.tr("Show component names"))
        self.smoothingCheckBox = QtGui.QCheckBox(self.tr("Use smoothing"))
        self.systrayCheckBox = QtGui.QCheckBox(self.tr("Use system tray (hide on close)"))
        self.gridCheckBox = QtGui.QCheckBox(self.tr("Show grid"))
        self.moveAlertCheckBox = QtGui.QCheckBox(self.tr("Alert on Move (when started)"))
        self.restoreLayoutCheckBox = QtGui.QCheckBox(self.tr("Remember and restore layout"))
        self.gnomeTerminalCheckBox = QtGui.QCheckBox(self.tr("Use Gnome-Terminal"))
        self.menuModsCheckBox = QtGui.QCheckBox(self.tr("Use white menu text"))

    def createBrowsables(self):
        """
        Create configurable color/image fields and associated buttons.
        """
        self.gridLine = LineEdit()
        self.chooseGridColorButton = QtGui.QPushButton("Choose")

        self.backgroundLine = LineEdit()
        self.browseBackgroundButton = QtGui.QPushButton("Browse")
        self.chooseBackgroundButton = QtGui.QPushButton("Choose")

        self.windowThemeLine = LineEdit()
        self.browseWindowThemeButton = QtGui.QPushButton("Browse")
        self.chooseWindowColorButton = QtGui.QPushButton("Choose")

        self.baseThemeLine = LineEdit()
        self.browseBaseThemeButton = QtGui.QPushButton("Browse")
        self.chooseBaseColorButton = QtGui.QPushButton("Choose")

    def browseForImage(self):
        """
        Browse for image in a file dialog.
        """
        # Qt is very picky in the filename structure but python is not, so we use python
        # to form the correct path which will work for both Windows and Linux
        old_dir = os.getcwd()
        os.chdir(environ["images"])
        load_path = os.getcwd()
        os.chdir(old_dir)

        return QtGui.QFileDialog.getOpenFileName(
            self,
            self.tr("Choose a file name"), load_path,
            self.tr("All Files(*.*);;PNG (*.PNG);;JPEG (*.JPG;*.JPEG);;GIF (*.GIF)")
        )

    def browseBackground(self):
        """
        Browse for background image.
        """
        filename = self.browseForImage()
        if filename:
            self.backgroundLine.setText(filename)

    def browseWindowTheme(self):
        """
        Browse for window theme image.
        """
        filename = self.browseForImage()
        if filename:
            self.windowThemeLine.setText(filename)

    def browseBaseTheme(self):
        """
        Browse for base theme image.
        """
        filename = self.browseForImage()
        if filename:
            self.baseThemeLine.setText(filename)

    def chooseColor(self, widget):
        """
        Choose a color from a color dialog.
        """
        color = QtGui.QColorDialog.getColor(QtCore.Qt.gray, self)
        if color.isValid():
            widget.setText("(%d,%d,%d)" % (color.red(), color.green(), color.blue()))

    def chooseBackgroundColor(self):
        """
        Choose a color for the background.
        """
        self.chooseColor(self.backgroundLine)

    def chooseWindowColor(self):
        """
        Choose a color for the window theme.
        """
        self.chooseColor(self.windowThemeLine)

    def chooseBaseColor(self):
        """
        Choose a color for the base theme.
        """
        self.chooseColor(self.baseThemeLine)

    def chooseGridColor(self):
        """
        Choose a color for the grid.
        """
        self.chooseColor(self.gridLine)

    def updateSettings(self):
        """
        Update the page with current options.
        """
        self.namesCheckBox.setChecked(options["names"])
        self.gridCheckBox.setChecked(options["grid"])
        self.moveAlertCheckBox.setChecked(options["moveAlert"])
        self.smoothingCheckBox.setChecked(options["smoothing"])
        self.systrayCheckBox.setChecked(options["systray"])
        self.restoreLayoutCheckBox.setChecked(options["restore"])
        self.gnomeTerminalCheckBox.setChecked(options["gnome"])
        self.menuModsCheckBox.setChecked(options["menumods"])

        self.gridLine.setText(options["gridColor"])
        self.backgroundLine.setText(options["background"])
        self.windowThemeLine.setText(options["windowTheme"])
        self.baseThemeLine.setText(options["baseTheme"])

    def getBrushFrom(self, option):
        """
        Get the color or image in brush form.
        """
        option = str(option)
        if option.startswith("(") and option.endswith(")"):
            try:
                r, g, b = option.strip("()").split(",", 2)
                return QtGui.QBrush(QtGui.QColor(int(r), int(g), int(b)))
            except:
                return QtGui.QBrush()
        else:
            return QtGui.QBrush(QtGui.QImage(option))

    def updateLook(self):
        """
        Update options that change the main user interface.
        """

        p = mainWidgets["app"].palette()

        if options["windowTheme"]:
            brush = self.getBrushFrom(options["windowTheme"])
        else:
            brush = defaultOptions["palette"].window()

        if options["baseTheme"]:
            brush2 = self.getBrushFrom(options["baseTheme"])
        else:
            brush2 = defaultOptions["palette"].base()

        palette = QtGui.QPalette(p.windowText(), p.button(), p.light(), p.dark(), p.mid(), p.text(), p.brightText(), brush2, brush)
        mainWidgets["app"].setPalette(palette)

    def saveOptions(self):
        """
        Save options handled by this page.
        """
        options["names"] = self.namesCheckBox.isChecked()
        options["grid"] = self.gridCheckBox.isChecked()
        options["smoothing"] = self.smoothingCheckBox.isChecked()
        options["systray"] = self.systrayCheckBox.isChecked()
        options["restore"] = self.restoreLayoutCheckBox.isChecked()
        options["moveAlert"] = self.moveAlertCheckBox.isChecked()
        options["gnome"] = self.gnomeTerminalCheckBox.isChecked()
        options["menumods"] = self.gnomeTerminalCheckBox.isChecked()

        options["gridColor"] = self.gridLine.text()
        options["background"] = self.backgroundLine.text()
        options["windowTheme"] = self.windowThemeLine.text()
        options["baseTheme"] = self.baseThemeLine.text()

        self.updateLook()


class RuntimePage(QtGui.QWidget):
    def __init__(self, parent=None):
        """
        Create a general configuration page.
        """
        super(RuntimePage, self).__init__(parent)

        compilationGroup = QtGui.QGroupBox(self.tr("Compilation / Runtime"))
        self.createCompilationCheckboxes()

        base_network_label = QtGui.QLabel(self.tr("Base network:"))
        self.base_network_input = LineEdit()

        base_network_layout = QtGui.QHBoxLayout()
        base_network_layout.addWidget(base_network_label)
        base_network_layout.addWidget(self.base_network_input)

        compilationLayout = QtGui.QVBoxLayout()
        compilationLayout.addWidget(self.autoroutingCheckBox)
        compilationLayout.addWidget(self.autogenCheckBox)
        compilationLayout.addWidget(self.autocompileCheckBox)
        compilationLayout.addWidget(self.glowingCheckBox)
        compilationLayout.addLayout(base_network_layout)
        compilationGroup.setLayout(compilationLayout)

        mainLayout = QtGui.QVBoxLayout()
        mainLayout.addWidget(compilationGroup)
        mainLayout.addSpacing(12)
        mainLayout.addStretch(1)

        self.setLayout(mainLayout)
        self.updateSettings()

    def createCompilationCheckboxes(self):
        """
        Create checkboxes for compilation options.
        """
        self.autoroutingCheckBox = QtGui.QCheckBox(self.tr("Auto-routing"))
        self.autogenCheckBox = QtGui.QCheckBox(self.tr("Auto-generate IP/MAC addresses"))
        self.autocompileCheckBox = QtGui.QCheckBox(self.tr("Compile before running"))
        self.glowingCheckBox = QtGui.QCheckBox(self.tr("Use glowing lights"))

    def updateSettings(self):
        """
        Update the page with current options.
        """
        self.autoroutingCheckBox.setChecked(options["autorouting"])
        self.autogenCheckBox.setChecked(options["autogen"])
        self.autocompileCheckBox.setChecked(options["autocompile"])
        self.glowingCheckBox.setChecked(options["glowingLights"])
        self.base_network_input.setText(options["base_network"])

    def saveOptions(self):
        """
        Save options handled by this page.
        """
        options["autorouting"] = self.autoroutingCheckBox.isChecked()
        options["autogen"] = self.autogenCheckBox.isChecked()
        options["autocompile"] = self.autocompileCheckBox.isChecked()
        options["glowingLights"] = self.glowingCheckBox.isChecked()
        if ip_utils.is_valid_base_network(self.base_network_input.text()):
            options["base_network"] = self.base_network_input.text()
        else:
            raise SaveConfigurationError(
                "Invalid network range!\n"
                "Classful network with prefix length equals 8 or 16 expected")


class ConfigDialog(QtGui.QDialog):
    def __init__(self, parent=None):
        """
        Create a config dialog window.
        """
        super(ConfigDialog, self).__init__(parent)

        self.wizard = None
        self.loadOptions()

        self.contentsWidget = QtGui.QListWidget()
        self.pagesWidget = QtGui.QStackedWidget()

        self.contentsWidget.setViewMode(QtGui.QListView.IconMode)
        self.contentsWidget.setIconSize(QtCore.QSize(96, 84))
        self.contentsWidget.setMovement(QtGui.QListView.Static)
        self.contentsWidget.setMaximumWidth(128)
        self.contentsWidget.setSpacing(12)

        self.generalPage = GeneralPage()
        self.runtimePage = RuntimePage()
        self.serverPage = ServerPage()
        self.pagesWidget.addWidget(self.generalPage)
        self.pagesWidget.addWidget(self.runtimePage)
        self.pagesWidget.addWidget(self.serverPage)

        self.createIcons()
        self.contentsWidget.setCurrentRow(0)

        apply_button = QtGui.QPushButton(self.tr("Apply"))
        close_button = QtGui.QPushButton(self.tr("Close"))

        self.connect(apply_button, QtCore.SIGNAL("clicked()"), self.apply_button_handler)
        self.connect(close_button, QtCore.SIGNAL("clicked()"), self, QtCore.SLOT("close()"))

        horizontalLayout = QtGui.QHBoxLayout()
        horizontalLayout.addWidget(self.contentsWidget)
        horizontalLayout.addWidget(self.pagesWidget, 1)

        buttonsLayout = QtGui.QHBoxLayout()
        buttonsLayout.addStretch(2)
        buttonsLayout.addWidget(apply_button)
        buttonsLayout.addWidget(close_button)

        mainLayout = QtGui.QVBoxLayout()
        mainLayout.addLayout(horizontalLayout)
        mainLayout.addStretch(1)
        mainLayout.addSpacing(12)
        mainLayout.addLayout(buttonsLayout)

        self.setLayout(mainLayout)
        self.setWindowModality(QtCore.Qt.ApplicationModal)
        self.setWindowTitle(self.tr("Configuration"))
        self.setWindowIcon(QtGui.QIcon(environ["images"] + "giniLogo.png"))

        if not self.wizard and options["autoconnect"]:
            mainWidgets["main"].startBackend()

    def show_update_error(self, error_message):
        QtGui.QMessageBox.critical(
            self,
            "Error!",
            error_message,
            QtGui.QMessageBox.Ok
        )

    def apply_button_handler(self):
        try:
            self.generalPage.saveOptions()
            self.runtimePage.saveOptions()
            self.serverPage.saveOptions()
        except SaveConfigurationError as e:
            self.show_update_error(str(e))
            return False

        try:
            outfile = open(environ["config"]+"settings", "w")
            project = mainWidgets["main"].getProject()
            if project:
                project_file = open(project, "w")
            for option, value in options.iteritems():
                if option in ["username", "server", "session"] and project:
                    project_file.write(option + "=" + str(value) + "\n")
                outfile.write(option + "=" + str(value) + "\n")
            if project:
                project_file.close()
            outfile.close()
        except:
            mainWidgets["log"].append("Cannot apply settings!")
            return False

        return True

    def loadOptions(self):
        """
        Load the options from file.
        """
        def parse(text):
            if text == "True":
                return True
            elif text == "False":
                return False
            else:
                return text

        try:
            if os.environ["SHELL"]:
                environ["os"] = "other"
        except:
            pass

        try:
            settingsFilename = environ["config"]+"settings"
            if not os.access(settingsFilename, os.F_OK):
                self.wizard = Wizard(self)
                self.wizard.show()
                return

            infile = open(settingsFilename, "r")
            settings = infile.readlines()
            infile.close()

            for line in settings:
                setting = line.strip()
                option, value = setting.split("=")
                options[option] = parse(value)

        except:
            mainWidgets["log"].append("Failed to load settings!")

    def changePage(self, current, previous):
        """
        Handle a page change.
        """
        if not current:
            current = previous

        self.pagesWidget.setCurrentIndex(self.contentsWidget.row(current))

    def createIcons(self):
        """
        Create the icons for the different pages.
        """
        generalButton = QtGui.QListWidgetItem(self.contentsWidget)
        generalButton.setIcon(QtGui.QIcon(environ["images"] + "config.png"))
        generalButton.setText(self.tr("General"))
        generalButton.setTextAlignment(QtCore.Qt.AlignHCenter)
        generalButton.setFlags(QtCore.Qt.ItemIsSelectable | QtCore.Qt.ItemIsEnabled)

        configButton = QtGui.QListWidgetItem(self.contentsWidget)
        configButton.setIcon(QtGui.QIcon(environ["images"] + "runtime.png"))
        configButton.setText(self.tr("Runtime"))
        configButton.setTextAlignment(QtCore.Qt.AlignHCenter)
        configButton.setFlags(QtCore.Qt.ItemIsSelectable | QtCore.Qt.ItemIsEnabled)

        configButton = QtGui.QListWidgetItem(self.contentsWidget)
        configButton.setIcon(QtGui.QIcon(environ["images"] + "Gserver.png"))
        configButton.setText(self.tr("Server"))
        configButton.setTextAlignment(QtCore.Qt.AlignHCenter)
        configButton.setFlags(QtCore.Qt.ItemIsSelectable | QtCore.Qt.ItemIsEnabled)

        self.connect(self.contentsWidget,
                     QtCore.SIGNAL("currentItemChanged(QListWidgetItem *, QListWidgetItem *)"), self.changePage)

    def closeEvent(self, event):
        """
        Handle closing the config window.
        """
        if not self.apply_button_handler():
            event.ignore()
            return
        return super(ConfigDialog, self).closeEvent(event)

    def updateSettings(self):
        """
        Update all pages with the current options.
        """
        self.generalPage.updateSettings()
        self.runtimePage.updateSettings()
        self.serverPage.updateSettings()


class Wizard(QtGui.QWizard):
    def __init__(self, parent=None):
        """
        Create a first time configuration wizard.
        """
        super(Wizard, self).__init__()

        self.parent = parent
        self.setWindowTitle("Initial Setup")

        self.page1 = QtGui.QWizardPage()
        self.page1.setTitle("Environment")

        self.systrayCheckBox = QtGui.QCheckBox("Use system tray (hide on close)")

        self.usernameLine = LineEdit(QtCore.QString(os.getlogin()))
        self.usernameLine.setToolTip("This username should be your login username to the\nserver or session you specify below.")

        self.serverLine = LineEdit(QtCore.QString("localhost"))
        self.serverLine.setToolTip("This server will run the GINI backend and connect to gbuilder.\nNote that this server must have the backend program installed.")

        self.sessionLine = LineEdit()
        self.sessionLine.setToolTip("If you are using Windows, you can specify a session instead\nof a server.  This session must be created within Putty.")

        self.autoconnectCheckBox = QtGui.QCheckBox("Automatically start server on gbuilder startup")
        self.autoconnectCheckBox.setChecked(True)

        self.serverLayout = QtGui.QGridLayout()
        self.serverLayout.addWidget(self.autoconnectCheckBox, 0, 0)
        self.serverLayout.addWidget(QtGui.QLabel("Username:"), 1, 0)
        self.serverLayout.addWidget(self.usernameLine, 1, 1)
        self.serverLayout.addWidget(QtGui.QLabel("Preferred Server:"), 2, 0)
        self.serverLayout.addWidget(self.serverLine, 2, 1)
        self.serverLayout.addWidget(QtGui.QLabel("\t\t"), 2, 2)
        self.serverLayout.addWidget(QtGui.QLabel("Preferred Session:"), 3, 0)
        self.serverLayout.addWidget(self.sessionLine, 3, 1)
        self.serverLayout.addWidget(self.systrayCheckBox, 4, 0)

        self.page1Layout = QtGui.QVBoxLayout()
        self.page1Layout.addLayout(self.serverLayout)
        self.page1Layout.addWidget(QtGui.QLabel())
        self.page1Layout.addWidget(QtGui.QLabel("Note: GINI only supports Linux hosts to run the backend server.\nIf you are running Windows, you can specify a session instead of a server.\nHover over the text fields for more information."))

        self.page1.setLayout(self.page1Layout)
        self.addPage(self.page1)

        self.setButtonText(self.FinishButton, "OK")
        self.setOption(self.NoBackButtonOnStartPage, True)
        self.setWindowModality(QtCore.Qt.ApplicationModal)
        self.setWindowIcon(QtGui.QIcon(environ["images"]+"giniLogo.png"))

    def accept(self):
        """
        Accept and save the options.
        """
        options["autoconnect"] = self.autoconnectCheckBox.isChecked()
        options["username"] = self.usernameLine.text()
        options["server"] = self.serverLine.text()
        options["session"] = self.sessionLine.text()
        options["systray"] = self.systrayCheckBox.isChecked()

        if options["server"] != "localhost":
            try:
                outfile = open(environ["config"]+"servers", "w")
                outfile.write(options["server"] + "\n")
                outfile.close()
            except:
                mainWidgets["log"].append("Failed to add server to list!")

        try:
            outfile = open(environ["config"]+"settings", "w")
            for option, value in options.iteritems():
                outfile.write(option + "=" + str(value) + "\n")
            outfile.close()
        except:
            mainWidgets["log"].append("Failed to save settings!")

        self.parent.updateSettings()

        QtGui.QWizard.accept(self)
