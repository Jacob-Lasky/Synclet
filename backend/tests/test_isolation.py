"""The suite never touches the live shares or app state (conftest._no_real_share)."""

from pathlib import Path


def test_tests_without_patch_paths_see_no_real_share(tmp_path: Path):
    from synclet import (
        config,
        fs_helpers,
        ignored,
        pending,
        plex,
        scan,
        state,
        sync_ops,
        watchstate,
    )

    for root in (
        config.MEDIA_ROOT,
        config.SYNC_ROOT,
        scan.MEDIA_ROOT,
        scan.SYNC_ROOT,
        sync_ops.MEDIA_ROOT,
        sync_ops.SYNC_ROOT,
        state.SYNC_ROOT,
        fs_helpers.SYNC_ROOT,
        pending.SYNC_ROOT,
        config.LIBRARY_LISTING_FILE,
        config.THUMB_CACHE,
        plex.THUMB_CACHE,
        plex.PLEX_CACHE_FILE,
        config.SNAPSHOT_FILE,
        pending.SNAPSHOT_FILE,
        config.IGNORED_FILE,
        ignored.IGNORED_FILE,
        config.FOLLOWED_FILE,
        config.WATCHSTATE_DB,
        watchstate.WATCHSTATE_DB,
    ):
        assert root.is_relative_to(tmp_path), root
