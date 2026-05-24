"""Tests for the shared TTL-cache primitive."""

from __future__ import annotations

import time

import pytest

from synclet import maint_cache


@pytest.fixture(autouse=True)
def _clean_cache():
    maint_cache.invalidate()
    yield
    maint_cache.invalidate()


class TestGetCached:
    def test_first_call_builds(self):
        build_calls = []

        def _build():
            build_calls.append(1)
            return "v1"

        assert maint_cache.get_cached("k", _build) == "v1"
        assert build_calls == [1]

    def test_second_call_within_ttl_returns_cached_value(self, monkeypatch):
        build_calls = []

        def _build():
            build_calls.append(1)
            return "v1"

        maint_cache.get_cached("k", _build)
        # Advance clock by less than TTL via monkeypatched time.
        original_time = time.time
        offset = maint_cache.STATE_CACHE_TTL - 1
        monkeypatch.setattr(
            "synclet.maint_cache.time.time", lambda: original_time() + offset
        )
        assert maint_cache.get_cached("k", _build) == "v1"
        assert build_calls == [1], "second call should hit cache"

    def test_second_call_after_ttl_rebuilds(self, monkeypatch):
        build_calls = []
        responses = iter(["v1", "v2"])

        def _build():
            build_calls.append(1)
            return next(responses)

        maint_cache.get_cached("k", _build)
        # Advance past TTL.
        original_time = time.time
        offset = maint_cache.STATE_CACHE_TTL + 1
        monkeypatch.setattr(
            "synclet.maint_cache.time.time", lambda: original_time() + offset
        )
        assert maint_cache.get_cached("k", _build) == "v2"
        assert build_calls == [1, 1]

    def test_ttl_measured_from_build_end_not_start(self, monkeypatch):
        """Regression: maint_cache used to stamp entries with build-START
        time. For long builds (synced ~25s on Jake's library) the cache
        would appear stale before the TTL window even nominally elapsed
        — the prefetch in main.warm_plex_caches would write at T+25s with
        timestamp T+0, so the FIRST user request 5s later saw an expired
        entry. The fix stamps with build-END time."""
        # Simulate a slow build that runs for almost the full TTL.
        clock = [1000.0]

        def _fake_time():
            return clock[0]

        monkeypatch.setattr("synclet.maint_cache.time.time", _fake_time)

        slow_seconds = maint_cache.STATE_CACHE_TTL - 5  # build burns most of TTL
        build_calls = []

        def _slow_build():
            build_calls.append(1)
            clock[0] += slow_seconds  # advance the clock during the build
            return "v1"

        maint_cache.get_cached("k", _slow_build)

        # Now advance "real time" by 10 seconds more — small relative to TTL,
        # but LARGE relative to start-time stamp + slow_seconds. With the
        # bug (start-time stamp), age = slow_seconds + 10 > TTL → cache miss.
        # With the fix (end-time stamp), age = 10 < TTL → cache hit.
        clock[0] += 10
        maint_cache.get_cached("k", _slow_build)
        assert build_calls == [1], (
            "build ran twice — entry was stamped with start-time, "
            "wasting the post-build TTL window"
        )

    def test_distinct_keys_are_independent(self):
        maint_cache.get_cached("a", lambda: 1)
        maint_cache.get_cached("b", lambda: 2)
        # Re-fetch — both should hit cache.
        a_calls, b_calls = [], []

        def _build_a():
            a_calls.append(1)
            return 99

        def _build_b():
            b_calls.append(1)
            return 99

        assert maint_cache.get_cached("a", _build_a) == 1
        assert maint_cache.get_cached("b", _build_b) == 2
        assert a_calls == []
        assert b_calls == []


class TestInvalidate:
    def test_clears_all_keys(self):
        maint_cache.get_cached("a", lambda: 1)
        maint_cache.get_cached("b", lambda: 2)
        maint_cache.invalidate()
        rebuilt = []
        maint_cache.get_cached("a", lambda: rebuilt.append("a") or 99)
        maint_cache.get_cached("b", lambda: rebuilt.append("b") or 99)
        assert rebuilt == ["a", "b"]
