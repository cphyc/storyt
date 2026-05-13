from __future__ import annotations

import logging
import os

_ENV_FLAG_TRUE = {"1", "true", "yes", "on"}


class _ColorFormatter(logging.Formatter):
    COLORS = {
        logging.DEBUG: "\033[36m",
        logging.INFO: "\033[32m",
        logging.WARNING: "\033[33m",
        logging.ERROR: "\033[31m",
        logging.CRITICAL: "\033[35m",
    }
    RESET = "\033[0m"

    def __init__(self, use_color: bool) -> None:
        super().__init__(
            fmt="[%(asctime)s] %(levelname)s %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        self.use_color = use_color

    def format(self, record: logging.LogRecord) -> str:
        msg = super().format(record)
        if not self.use_color:
            return msg
        color = self.COLORS.get(record.levelno)
        if color is None:
            return msg
        return f"{color}{msg}{self.RESET}"


def _env_enabled() -> bool:
    return os.getenv("STORYT_DISCOVER_LOG", "").lower() in _ENV_FLAG_TRUE


logger = logging.getLogger("storyt")

if not logger.handlers:
    handler = logging.StreamHandler()
    use_color = os.getenv("NO_COLOR") is None
    handler.setFormatter(_ColorFormatter(use_color=use_color))
    logger.addHandler(handler)
    logger.propagate = False


def configure_logging(enabled: bool | None = None) -> None:
    """Configure project-wide logging for storyt."""
    enabled = _env_enabled() if enabled is None else enabled
    logger.setLevel(logging.DEBUG if enabled else logging.ERROR)


configure_logging()
