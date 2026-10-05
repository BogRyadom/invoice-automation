"""Worker process entry point. Queue processing (FOR UPDATE SKIP LOCKED) arrives in Stage 3."""

import logging
import signal
import threading

from app.config import get_settings
from app.db import create_db_engine, ping
from app.log import configure_logging

logger = logging.getLogger(__name__)


def run(stop: threading.Event, poll_interval: float) -> int:
    """Poll until stop is set. Returns the number of completed iterations."""
    iterations = 0
    while not stop.is_set():
        iterations += 1
        logger.debug("worker poll %d", iterations)
        stop.wait(poll_interval)
    return iterations


def main() -> None:
    """Start the worker and stop gracefully on SIGINT or SIGTERM."""
    settings = get_settings()
    configure_logging(settings.log_level)
    engine = create_db_engine(settings.database_url)
    ping(engine)

    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())

    logger.info("worker started")
    run(stop, settings.worker_poll_interval_seconds)
    engine.dispose()
    logger.info("worker stopped")


if __name__ == "__main__":
    main()
