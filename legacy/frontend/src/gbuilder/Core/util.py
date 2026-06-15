""" Various utility functions """

import os


def progExists(prog):
    """
    Check if the given program exists in the executable path.
    Return True if program found, false otherwise
    """
    for path in os.environ["PATH"].split(os.pathsep):
        file_name = os.path.join(path, prog)
        if os.path.exists(file_name) and os.access(file_name, os.X_OK):
            return True
    return False
