"""Entry point for backgrounded daemon process.

Invoked by: python -m ipod_sync.daemon
Handles: PID file writing, signal cleanup, and running DaemonRunner.
"""

import os
import sys

from ipod_sync.config import Config, PID_FILE, ensure_dirs
from ipod_sync.daemon.runner import DaemonRunner


def main() -> None:
    foreground = "--foreground" in sys.argv

    ensure_dirs()
    PID_FILE.write_text(str(os.getpid()))

    try:
        config = Config.load()
        DaemonRunner(config, foreground=foreground).run()
    finally:
        PID_FILE.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
