"""
Structured Logging Module for EMA + VWAP Trading System.
Configures rotating log files for app logs (logs/app.log) and paper trading logs (logs/paper.log).
Supports JSON structured logging with trace_id correlation for distributed tracing.
"""

import json
import logging
import os
import threading
import uuid
from logging.handlers import RotatingFileHandler

LOGS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
APP_LOG_PATH = os.path.join(LOGS_DIR, "app.log")
PAPER_LOG_PATH = os.path.join(LOGS_DIR, "paper.log")

# Thread-local storage for trace_id
_trace_local = threading.local()


def get_trace_id() -> str:
    """Get or generate trace_id for current thread."""
    if not hasattr(_trace_local, "trace_id"):
        _trace_local.trace_id = str(uuid.uuid4())[:8]
    return _trace_local.trace_id


def set_trace_id(trace_id: str) -> None:
    """Set trace_id for current thread."""
    _trace_local.trace_id = trace_id


def clear_trace_id() -> None:
    """Clear trace_id for current thread."""
    if hasattr(_trace_local, "trace_id"):
        del _trace_local.trace_id


class JSONFormatter(logging.Formatter):
    """JSON formatter with trace_id correlation for structured logging."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "thread": record.threadName,
        }

        # Add trace_id if available
        trace_id = getattr(record, "trace_id", None) or get_trace_id()
        if trace_id:
            log_entry["trace_id"] = trace_id

        # Add extra fields from record
        for key, value in record.__dict__.items():
            if key not in (
                "name",
                "msg",
                "args",
                "created",
                "filename",
                "funcName",
                "levelname",
                "levelno",
                "lineno",
                "module",
                "msecs",
                "message",
                "name",
                "pathname",
                "process",
                "processName",
                "relativeCreated",
                "thread",
                "threadName",
                "exc_info",
                "exc_text",
                "stack_info",
                "trace_id",
            ):
                log_entry[key] = value

        # Add exception info if present
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_entry, default=str)


class BindMountSafeRotatingFileHandler(RotatingFileHandler):
    """RotatingFileHandler that survives Docker Desktop bind mounts.

    On Windows bind mounts (gRPC-FUSE/VirtioFS), os.rename() inside the
    container can fail with EACCES. Stock RotatingFileHandler then raises on
    every emit once maxBytes is exceeded, silently dropping every subsequent
    log record. This subclass falls back to copy-then-truncate so rollover
    always succeeds and records keep flowing.
    """

    def doRollover(self) -> None:
        if self.stream:
            self.stream.close()
            self.stream = None
        if self.backupCount > 0:
            dfn = self.rotation_filename(self.baseFilename + ".1")
            for i in range(self.backupCount - 1, 0, -1):
                sfn = self.rotation_filename(f"{self.baseFilename}.{i}")
                dest = self.rotation_filename(f"{self.baseFilename}.{i + 1}")
                if os.path.exists(sfn):
                    self._shift(sfn, dest)
            try:
                if os.path.exists(dfn):
                    os.remove(dfn)
            except OSError:
                pass
            self._shift(self.baseFilename, dfn)
        if not self.delay:
            self.stream = self._open()

    def _shift(self, source: str, dest: str) -> None:
        """Move a log file, falling back to copy+truncate when rename fails."""
        try:
            os.rename(source, dest)
        except OSError:
            try:
                with (
                    open(source, "rb") as src_fh,
                    open(dest, "wb") as dst_fh,
                ):
                    while True:
                        chunk = src_fh.read(1024 * 1024)
                        if not chunk:
                            break
                        dst_fh.write(chunk)
                with open(source, "r+b") as fh:
                    fh.truncate(0)
            except OSError:
                # Last resort: keep writing in place rather than losing records.
                pass


def setup_logging(
    log_level: int = logging.INFO,
    max_bytes: int = 10 * 1024 * 1024,  # 10 MB per log file
    backup_count: int = 5,
) -> None:
    """Initialize rotating file handlers and console loggers.

    Uses JSON formatter when LOG_JSON=true environment variable is set,
    otherwise uses human-readable format.
    """
    os.makedirs(LOGS_DIR, exist_ok=True)

    # Use JSON formatter if LOG_JSON=true
    use_json = os.getenv("LOG_JSON", "false").lower() == "true"

    if use_json:
        formatter = JSONFormatter(datefmt="%Y-%m-%d %H:%M:%S")
    else:
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
        app_file_handler = BindMountSafeRotatingFileHandler(
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
        paper_file_handler = BindMountSafeRotatingFileHandler(
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
