#!/usr/bin/python2

"""
gini_module_load.py

Watches a particular file for the addition of new module names. The modules
with those names are imported into POX and executed. This allows modules
to be loaded from gBuilder.
"""

from pox.core import core
import pox.lib.util as poxutil
import pox.openflow.libopenflow_01 as of
from pox.openflow import ConnectionUp
import os
import time
import threading
import importlib
import traceback

log = core.getLogger()

class ModuleFileReader(threading.Thread):
    def __init__(self, module_file_path):
        threading.Thread.__init__(self)
        self.module_file_path = module_file_path
        self.current_module = None

    def _flush_rules(self):
        log.info("remove current handlers and flows..")
        core.openflow._eventMixin_handlers.clear()

        msg = of.ofp_flow_mod(match=of.ofp_match(), command=of.OFPFC_DELETE)
        for connection in core.openflow._connections.values():
            connection.send(msg)
        del self.current_module

    def run(self):
        """
        Rewritten by Trung

        The current implementation of run() is supposed to load only one Gini
        module at a time. To load a new module, the current one must be unloaded
        and cleaned from the core object.

        To unload the current module, all the rules stored in core.openflow will
        be flushed, and all connected switches should also reset their flow tables

        If the module has registered a new component in core object, then that
        component could be deleted as well, although this step may require some
        addition to the third-party code.
        """
        while True:
            time.sleep(5)

            try:
                module_file = open(self.module_file_path, "r")
                module = module_file.readline().strip()

                if module == "":
                    pass
                elif self.current_module and module == self.current_module.__name__:
                    pass
                else:
                    self._flush_rules()

                    log.info("loading module - " + module)
                    try:
                        mod = importlib.import_module(module)
                        try:
                            mod.launch()
                            self.current_module = mod
                            # workaround for the case the new module only handles new connections
                            for conn in core.openflow._connections.values():
                                core.openflow.raiseEvent(ConnectionUp, conn, conn.features)
                            log.info("module %s loaded" % module)
                        except Exception as e:
                            log.error("failed to launch module - " + module)
                            log.error(traceback.format_exc())
                    except Exception as e:
                        log.error("module does not exist - " + module)
                        log.error(traceback.format_exc())

                module_file.close()
            except Exception as e:
                pass

@poxutil.eval_args
def launch(module_file_path):
    module_file_reader = ModuleFileReader(module_file_path)
    module_file_reader.start()
