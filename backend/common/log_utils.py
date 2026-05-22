"""Module-aware logger wrapper that prefixes each record with the caller's
function name, since logging.Formatter has no native frame-pointer access.

`get_logger()` is the only public surface; modules should not instantiate
ContextualLogger directly. The `setLoggerClass` call below makes every
logger created via `logging.getLogger` (including those from third-party
libs) inherit the context-prefix behavior.
"""

from __future__ import annotations

import inspect
import logging
import os
from typing import Any, cast


class ContextualLogger(logging.Logger):
    def _add_context(self, msg: object) -> str:
        # Two .f_back hops: this method's frame → debug/info/etc → caller.
        frame = inspect.currentframe()
        if frame is None or frame.f_back is None or frame.f_back.f_back is None:
            return str(msg)
        func_name = frame.f_back.f_back.f_code.co_name
        return f"[{func_name}]: {msg}"

    def debug(self, msg: object, *args: object, **kwargs: Any) -> None:
        super().debug(self._add_context(msg), *args, **kwargs)

    def info(self, msg: object, *args: object, **kwargs: Any) -> None:
        super().info(self._add_context(msg), *args, **kwargs)

    def warning(self, msg: object, *args: object, **kwargs: Any) -> None:
        super().warning(self._add_context(msg), *args, **kwargs)

    def error(self, msg: object, *args: object, **kwargs: Any) -> None:
        super().error(self._add_context(msg), *args, **kwargs)

    def critical(self, msg: object, *args: object, **kwargs: Any) -> None:
        super().critical(self._add_context(msg), *args, **kwargs)

    def exception(self, msg: object, *args: object, **kwargs: Any) -> None:
        super().exception(self._add_context(msg), *args, **kwargs)


logging.setLoggerClass(ContextualLogger)


def get_logger(name: str = __name__) -> ContextualLogger:
    # logging.getLogger returns Logger statically; setLoggerClass above
    # guarantees the runtime instance is ContextualLogger.
    logger = cast("ContextualLogger", logging.getLogger(name))

    # Avoid duplicate handlers
    if not logger.handlers:
        handler = logging.StreamHandler()

        log_format = os.getenv(
            "LOG_FORMAT",
            "%(asctime)s | %(levelname)s:\t%(message)s",
        )
        formatter = logging.Formatter(
            fmt=log_format,
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

        # Set level from environment or default to INFO
        log_level_str = os.getenv("LOG_LEVEL", "INFO").upper()
        log_level = getattr(logging, log_level_str, logging.INFO)
        logger.setLevel(log_level)

    return logger
