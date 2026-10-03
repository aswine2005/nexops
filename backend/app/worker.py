"""Standalone monitor process: `python -m app.worker` (run one or more; a database lock elects the leader)."""
from __future__ import annotations

import logging
import signal
import sys

from app.bootstrap import bootstrap
from app.services.monitor import MonitorThread


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    bootstrap()
    monitor = MonitorThread()

    def stop(*_args: object) -> None:
        logging.getLogger(__name__).info("Stopping NexOps monitor")
        monitor.stop()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    monitor.start()
    while monitor.is_alive():
        monitor.join(timeout=1.0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
