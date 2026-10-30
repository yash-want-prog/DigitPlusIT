"""Structured application logging for HandoffIQ."""
import contextvars
import json
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

request_id_var = contextvars.ContextVar("request_id", default="-")


class JsonFmt(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "request_id": request_id_var.get(),
            "msg": record.getMessage(),
        }
        context = getattr(record, "ctx", None)
        if isinstance(context, dict):
            data.update(context)
        if record.exc_info:
            data["exc"] = self.formatException(record.exc_info)
        return json.dumps(data, ensure_ascii=False, default=str)


def _log_path() -> Path:
    configured = Path(os.getenv("LOG_FILE", "logs/app.log")).expanduser()
    if not configured.is_absolute():
        configured = Path(__file__).resolve().parents[1] / configured
    return configured.resolve()


def setup() -> Path:
    """Configure HandoffIQ logging once; write JSON logs to file and console."""
    path = _log_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("handoffiq")
    logger.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
    logger.propagate = False

    wanted = str(path)
    has_file = any(getattr(h, "_handoffiq_file", None) == wanted for h in logger.handlers)
    has_console = any(getattr(h, "_handoffiq_console", False) for h in logger.handlers)
    formatter = JsonFmt()

    if not has_file:
        handler = RotatingFileHandler(
            path, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
        )
        handler.setFormatter(formatter)
        handler._handoffiq_file = wanted
        logger.addHandler(handler)

    if not has_console:
        handler = logging.StreamHandler()
        handler.setFormatter(formatter)
        handler._handoffiq_console = True
        logger.addHandler(handler)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    return path
