"""
Structured Logging Module for EMA + VWAP Trading System.
Configures rotating log files for app logs (logs/app.log) and paper trading logs (logs/paper.log).
"""

import logging
import os
from logging.handlers import RotatingFileHandler

LOGS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
APP_LOG_PATH = os.path.join(LOGS_DIR, "app.log")
PAPER_LOG_PATH = os.path.join(LOGS_DIR, "paper.log")


def setup_logging(
    log_level: int = logging.INFO,
    max_bytes: int = 10 * 1024 * 1024,  # 10 MB per log file
    backup_count: int = 5,
) -> None:
    """Initialize rotating file handlers and console loggers."""
    os.makedirs(LOGS_DIR, exist_ok=True)

    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [%(name)s:%(threadName)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Root Logger Configuration
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Avoid duplicate handlers if setup_logging is called multiple times
    existing_handler_names = {h.name for h in root_logger.handlers if h.name}

    if "app_file_handler" not in existing_handler_names:
        app_file_handler = RotatingFileHandler(
            APP_LOG_PATH,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        app_file_handler.name = "app_file_handler"
        app_file_handler.setLevel(log_level)
        app_file_handler.setFormatter(formatter)
        root_logger.addHandler(app_file_handler)

    if "console_handler" not in existing_handler_names:
        console_handler = logging.StreamHandler()
        console_handler.name = "console_handler"
        console_handler.setLevel(log_level)
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)

    # Specialized Paper Trading Logger
    paper_logger = logging.getLogger("paper_trading")
    paper_logger.setLevel(log_level)
    paper_logger.propagate = True  # Send to root app.log & console as well

    paper_handler_names = {h.name for h in paper_logger.handlers if h.name}
    if "paper_file_handler" not in paper_handler_names:
        paper_file_handler = RotatingFileHandler(
            PAPER_LOG_PATH,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        paper_file_handler.name = "paper_file_handler"
        paper_file_handler.setLevel(log_level)
        paper_file_handler.setFormatter(formatter)
        paper_logger.addHandler(paper_file_handler)


# Application Main Logger & Dedicated Paper Trading Logger
logger = logging.getLogger("ema_vwap")
paper_logger = logging.getLogger("paper_trading")
