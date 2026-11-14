"""Entry point of the packaged MeshLens.exe: runs as a tray app unless other arguments are given.

The packaged app has no console, so an unhandled error would pop up a crash dialog (and during an
update that dialog blocks the installer). Errors are logged instead and the process exits.
"""
import logging
import os
import sys

from meshlens.__main__ import main

if __name__ == "__main__":
    if len(sys.argv) == 1:
        sys.argv.append("--tray")
    try:
        main()
    except SystemExit:
        raise
    except BaseException:
        logging.getLogger("meshlens").exception("MeshLens stopped after an unexpected error")
        logging.shutdown()
        os._exit(1)
