"""Entry point of the packaged MeshLens.exe: runs as a tray app unless other arguments are given."""
import sys

from meshlens.__main__ import main

if __name__ == "__main__":
    if len(sys.argv) == 1:
        sys.argv.append("--tray")
    main()
