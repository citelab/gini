#!/usr/bin/python2

"""
gini_pid.py
   
Retrieves the OpenFlow controller PID and writes it to a file. The PID is used
to determine which TCP port the controller is listening on so that gRouter can
connect to it.
"""

from pox.core import core
import pox.lib.util as poxutil
import os

log = core.getLogger()

@poxutil.eval_args
def launch(pid_file_path):
    # Retrieve the PID of this instance of POX
    pid = os.getpid()
    log.info("gini_pid: pid - " + str(pid))

    # Write the PID to the specified file
    try:
        os.remove(pid_file_path)
    except OSError:
        pass
    pid_file = open(pid_file_path, "w")
    pid_file.write(str(pid))
    pid_file.close()
