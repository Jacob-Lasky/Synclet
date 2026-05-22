"""Shared HTTP mock helpers for tests that exercise urllib.request.

The synclet codebase still uses urllib.request directly (the migration to
httpx is a follow-up). Tests for plex.py, watchlist.py, and the routes that
proxy them all need the same context-manager-returning callable. Centralized
here so the same fixture shape isn't reinvented per file.
"""

from __future__ import annotations

from collections.abc import Mapping


class FakeUrlopenResponse:
    """Stand-in for the object urllib.request.urlopen returns.

    Implements the protocol consumed by callers in synclet:
      .read()             -> raw bytes
      .status             -> int (scrobble checks 200 <= status < 300)
      .headers.get(name)  -> string (poster routes read Content-Type)
      .__enter__/__exit__ -> context-manager support
    """

    def __init__(
        self,
        payload: bytes = b"",
        *,
        status: int = 200,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self._payload = payload
        self.status = status
        self.headers = dict(headers or {"Content-Type": "image/jpeg"})

    def __enter__(self) -> FakeUrlopenResponse:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return self._payload


def fake_urlopen(
    payload: bytes = b"",
    *,
    status: int = 200,
    headers: Mapping[str, str] | None = None,
):
    """Return a callable suitable for monkeypatching urllib.request.urlopen.

    Each call yields a fresh FakeUrlopenResponse, so the context-manager
    can be re-entered across multiple urlopen invocations in one test.
    """

    def _open(*_args: object, **_kwargs: object) -> FakeUrlopenResponse:
        return FakeUrlopenResponse(payload, status=status, headers=headers)

    return _open


def boom_urlopen(message: str = "network gone"):
    """A urlopen callable that always raises OSError. Mirrors what
    urllib.request.urlopen does on DNS failure or connection refused.
    """

    def _open(*_args: object, **_kwargs: object):
        raise OSError(message)

    return _open
