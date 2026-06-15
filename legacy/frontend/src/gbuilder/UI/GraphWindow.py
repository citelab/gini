import numpy
import warnings
warnings.simplefilter('ignore', numpy.RankWarning)

from numpy.lib.polynomial import *
from matplotlib.backends.backend_qt4agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qt4agg import NavigationToolbar2QTAgg as NavigationToolbar
from matplotlib.figure import Figure

from PyQt4 import QtCore, QtGui
from Core.globals import mainWidgets
from Dockable import *


class RouterQueue:
    def __init__(self):
        self.lastIndex = 0
        self.x = []
        self.y = {"size": [], "rate": []}
        self.name = None

    def getName(self):
        return self.name

    def setName(self, name):
        self.name = name

    def inc(self):
        self.lastIndex += 1

    def addPoint(self, ysize, yrate):
        if len(self.x) > 9:
            self.x.pop(0)
            self.y["size"].pop(0)
            self.y["rate"].pop(0)

        self.x.append(self.lastIndex)
        self.y["size"].append(ysize)
        self.y["rate"].append(yrate)

        self.inc()

    def getSizes(self):
        return self.y["size"]

    def getRates(self):
        return self.y["rate"]

    def getX(self):
        return self.x


class GraphWindow(Dockable):
    def __init__(self, name, parent = None):
        """
        Create a stats window to view mobile statistics.
        """
        super(GraphWindow, self).__init__("Graph of" + name, parent)

        self.name = name
        self.setMinimumSize(600,500)
        self.resize(600,500)
        self.setFloating(True)
        self.setAllowedAreas(QtCore.Qt.NoDockWidgetArea)
        self.create_main_frame()

        self.smoothing = False
        self.lastIndex = 0
        self.queues = {
            "outputQueue": RouterQueue(),
            "default":RouterQueue()
        }

        self.setWidget(self.main_frame)

        self.timer = QtCore.QTimer()
        self.connect(self.timer, QtCore.SIGNAL("timeout()"), self.refresh)
        self.connect(self,
                     QtCore.SIGNAL("topLevelChanged(bool)"),
                     self.dockChanged)
        self.timer.start(1000)

    def dockChanged(self, floating):
        if not floating:
            self.setFloating(True)

    def closeEvent(self, event):
        self.timer.stop()
        QtGui.QDockWidget.closeEvent(self, event)

    def refresh(self):
        client = mainWidgets["client"]
        if client:
            client.send("rstats " + self.name)

    def updateStats(self, queueName, size, rate):
        if queueName not in self.queues:
            self.queues[queueName] = RouterQueue()
        queue = self.queues[queueName]
        queue.addPoint(float(size), float(rate))

        self.on_draw()

    def split(self, y):
        above = False
        below = False

        initial = y[0]
        for i in range(len(y)):
            if above:
                if y[i] <= initial/2:
                    return y[:i], y[i:]
            elif below:
                if y[i] >= initial/2:
                    return y[:i], y[i:]
            elif y[i] > initial:
                above = True
            elif y[i] < initial:
                below = True

        return y, []

    def divide(self, y):
        part1, part2 = self.split(y)
        parts = [part1]
        while part2:
            part1, part2 = self.split(part2)
            parts.append(part1)

        return parts

    def toggleSmooth(self):
        self.smoothing = not self.smoothing

    def smooth(self, x, y):
        if not self.smoothing or len(x) < 2:
            return x, y

        parts = self.divide(y)
        count = 0
        tx = []
        ty = []
        for ypart in parts:
            xpart = x[count:count+len(ypart)]
            count += len(ypart)
            p = polyfit(xpart, ypart, 2)
            dx = numpy.linspace(xpart[0], xpart[-1], 2*len(xpart))
            dy = polyval(p, dx)

            tx += dx.tolist()
            ty += dy.tolist()

        return tx, ty

    def on_draw(self):
        """ Redraws the figure
        """
        # clear the axes and redraw the plot anew
        #
        for i in range(len(self.queues) % 5):
            axes = self.axesList[i]
            queue = self.queues.values()[i]

            axes.clear()
            axes.grid(self.grid_cb.isChecked())
            axes.set_title(self.queues.keys()[i])

            x,y = self.smooth(queue.getX(), queue.getSizes())
            x2,y2 = self.smooth(queue.getX(), queue.getRates())

            axes.plot(
                x,
                y,
                antialiased=True,
                lw=3)

            axes.plot(
                x2,
                y2,
                antialiased=True,
                lw=3)

        for canvas in self.canvases:
            canvas.draw()

    def create_main_frame(self):
        self.main_frame = QtGui.QWidget()

        # Create the mpl Figure and FigCanvas objects.
        # 5x4 inches, 100 dots-per-inch
        #
        self.dpi = 100
        self.figs = []
        self.canvases = []
        self.axesList = []

        for i in range(4):
            fig = Figure((3.0, 2.0), dpi=self.dpi)
            canvas = FigureCanvas(fig)
            canvas.setParent(self.main_frame)
            axes = fig.add_subplot(111)
            axes.set_ylim(-1.0, 1.0)
            self.figs.append(fig)
            self.canvases.append(canvas)
            self.axesList.append(axes)

        # Other GUI controls
        #
        self.smooth_button = QtGui.QPushButton("&Toggle Smoothing")
        self.connect(self.smooth_button, QtCore.SIGNAL('clicked()'), self.toggleSmooth)

        self.grid_cb = QtGui.QCheckBox("Show &Grid")
        self.grid_cb.setChecked(False)
        self.connect(self.grid_cb, QtCore.SIGNAL('stateChanged(int)'), self.on_draw)

        self.legend = QtGui.QLabel("Blue: Queue Sizes\nGreen: Queue Rates")
        #
        # Layout with box sizers
        #
        hbox = QtGui.QHBoxLayout()

        for w in [self.grid_cb, self.smooth_button, self.legend]:
            hbox.addWidget(w)
            hbox.setAlignment(w, QtCore.Qt.AlignVCenter)

        vbox = QtGui.QGridLayout()
        vbox.addWidget(self.canvases[0], 0, 0)
        vbox.addWidget(self.canvases[1], 0, 1)
        vbox.addWidget(self.canvases[2], 1, 0)
        vbox.addWidget(self.canvases[3], 1, 1)
        vbox.addLayout(hbox, 3, 0)

        self.main_frame.setLayout(vbox)
        self.setWidget(self.main_frame)
