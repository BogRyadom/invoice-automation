import logging


def configure_logging(level: str) -> None:
    """Configure root logging for the API and worker processes."""
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        force=True,
    )
