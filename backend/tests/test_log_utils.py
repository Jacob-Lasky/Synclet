"""Tests for common.log_utils , the contextual logger wrapper.

Two surfaces under test: ContextualLogger.{debug,info,...} prefixes the
caller's function name; get_logger wires a handler + level from env vars.
"""

from __future__ import annotations

import logging

import pytest

from common import log_utils


@pytest.fixture(autouse=True)
def _isolate_logger():
    """Each test gets a fresh logger so handler accumulation across tests
    doesn't make the 'avoid duplicate handlers' branch untestable.
    """
    # Drop any handlers added by previous tests on the shared root caches.
    yield
    for name in list(logging.Logger.manager.loggerDict):
        if name.startswith("test_log_utils."):
            del logging.Logger.manager.loggerDict[name]


class TestGetLogger:
    def test_returns_contextual_logger(self):
        logger = log_utils.get_logger("test_log_utils.basic")
        assert isinstance(logger, log_utils.ContextualLogger)

    def test_adds_stream_handler_on_first_call(self):
        logger = log_utils.get_logger("test_log_utils.handler")
        assert any(isinstance(h, logging.StreamHandler) for h in logger.handlers)

    def test_second_call_does_not_duplicate_handlers(self):
        logger1 = log_utils.get_logger("test_log_utils.dedup")
        initial_count = len(logger1.handlers)
        logger2 = log_utils.get_logger("test_log_utils.dedup")
        # Same logger instance via Python's logging singleton
        assert logger1 is logger2
        assert len(logger2.handlers) == initial_count

    def test_respects_log_level_env_var(self, monkeypatch):
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")
        logger = log_utils.get_logger("test_log_utils.level_debug")
        assert logger.level == logging.DEBUG

    def test_unknown_log_level_falls_back_to_info(self, monkeypatch):
        monkeypatch.setenv("LOG_LEVEL", "GARBAGE")
        logger = log_utils.get_logger("test_log_utils.level_bad")
        assert logger.level == logging.INFO

    def test_respects_log_format_env_var(self, monkeypatch):
        monkeypatch.setenv("LOG_FORMAT", "%(message)s")
        logger = log_utils.get_logger("test_log_utils.format")
        # Pull the formatter off the handler we just added
        fmt = logger.handlers[0].formatter
        assert fmt is not None
        assert fmt._fmt == "%(message)s"


class TestContextualLogger:
    def _call_level(self, level: str, caplog):
        # Helper: the f_back walk needs an actual caller frame, so we wrap
        # the log call in a named function to assert the prefix matches.
        logger = log_utils.get_logger(f"test_log_utils.ctx_{level}")
        with caplog.at_level(logging.DEBUG, logger=logger.name):

            def _named_caller():
                getattr(logger, level)("payload")

            _named_caller()
        return caplog.records

    def test_debug_prefixes_caller_name(self, caplog):
        records = self._call_level("debug", caplog)
        assert any("[_named_caller]: payload" in r.getMessage() for r in records)

    def test_info_prefixes_caller_name(self, caplog):
        records = self._call_level("info", caplog)
        assert any("[_named_caller]: payload" in r.getMessage() for r in records)

    def test_warning_prefixes_caller_name(self, caplog):
        records = self._call_level("warning", caplog)
        assert any("[_named_caller]: payload" in r.getMessage() for r in records)

    def test_error_prefixes_caller_name(self, caplog):
        records = self._call_level("error", caplog)
        assert any("[_named_caller]: payload" in r.getMessage() for r in records)

    def test_critical_prefixes_caller_name(self, caplog):
        records = self._call_level("critical", caplog)
        assert any("[_named_caller]: payload" in r.getMessage() for r in records)

    def test_exception_prefixes_caller_name(self, caplog):
        logger = log_utils.get_logger("test_log_utils.exception")
        with caplog.at_level(logging.ERROR, logger=logger.name):

            def _named_caller():
                try:
                    msg = "boom"
                    raise ValueError(msg)
                except ValueError:
                    logger.exception("caught it")

            _named_caller()
        assert any(
            "[_named_caller]: caught it" in r.getMessage() for r in caplog.records
        )
