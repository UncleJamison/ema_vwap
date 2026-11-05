"""
Unit tests for structured rotating logging module.
"""

import logging
import os

from src.logger import (
    APP_LOG_PATH,
    LOGS_DIR,
    PAPER_LOG_PATH,
    logger,
    setup_logging,
)


def test_logging_setup_and_writing():
    # Clear existing handlers to avoid interference from other tests
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    paper_logger = logging.getLogger("paper_trading")
    for handler in paper_logger.handlers[:]:
        paper_logger.removeHandler(handler)

    setup_logging(log_level=logging.DEBUG)

    logger.info("Test main app logger message")
    paper_logger.info("Test paper logger message")

    assert os.path.exists(LOGS_DIR)
    assert os.path.exists(APP_LOG_PATH)
    assert os.path.exists(PAPER_LOG_PATH)

    with open(APP_LOG_PATH, "r", encoding="utf-8") as f:
        app_content = f.read()
        assert "Test main app logger message" in app_content

    with open(PAPER_LOG_PATH, "r", encoding="utf-8") as f:
        paper_content = f.read()
        assert "Test paper logger message" in paper_content
