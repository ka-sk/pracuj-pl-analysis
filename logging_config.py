import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_FORMAT = (
    "%(filename)s:%(lineno)d | %(asctime)s | %(levelname)-8s | "
    "%(funcName)s | %(message)s"
)


def setup_logging(log_file: str = "logs/scraper.log", level: int = logging.INFO) -> None:
    """Call once, at program start. Every module then just uses logging.getLogger(__name__)."""
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(LOG_FORMAT, datefmt="%Y-%m-%d %H:%M:%S")

    file_handler = RotatingFileHandler(
        log_file, maxBytes=5_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()          # avoid duplicate lines if called twice
    root.addHandler(file_handler)
    root.addHandler(console)

    logging.getLogger("urllib3").setLevel(logging.WARNING)  # silence noisy lib