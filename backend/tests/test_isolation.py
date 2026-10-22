"""The suite never touches the live shares (see conftest._no_real_share)."""

from pathlib import Path


def test_tests_without_patch_paths_see_no_real_share(tmp_path: Path):
    from synclet import config, fs_helpers, pending, scan, state, sync_ops

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
    ):
        assert root.is_relative_to(tmp_path), root
