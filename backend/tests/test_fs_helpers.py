"""Verify the dedup logic in iter_sync_subs (tv + tv-4kUHD share `tv` sub)."""

import contextlib

from synclet.fs_helpers import (
    iter_sync_subs,
    iter_synced_titles,
    synced_title_sizes,
)


class TestIterSyncSubs:
    def test_yields_existing_subs_only(self, patch_paths):
        subs = list(iter_sync_subs())
        names = [s.name for s in subs]
        # `tv` exists (fixture pre-synced After Life). `movies` and `youtube`
        # do not exist in the fixture sync tree, so they should be skipped.
        assert "tv" in names
        # No duplicates
        assert len(names) == len(set(names))

    def test_handles_missing_sync_root(self, monkeypatch, tmp_path):
        monkeypatch.setattr("synclet.fs_helpers.SYNC_ROOT", tmp_path / "nope")
        assert list(iter_sync_subs()) == []


class TestIterSyncedTitles:
    def test_yields_synced_titles(self, patch_paths):
        titles = list(iter_synced_titles())
        names = [t[1].name for t in titles]
        assert "After Life (2019) {tvdb-2}" in names

    def test_skips_dotfiles(self, patch_paths):
        sync = patch_paths["sync"]
        (sync / "tv" / ".hidden").mkdir()
        titles = list(iter_synced_titles())
        names = [t[1].name for t in titles]
        assert ".hidden" not in names


class TestSyncedTitleSizes:
    """Parity tests for the single-walk byte aggregator. The function
    replaces a per-title rglob+stat loop in /api/synced; the contract is
    that the totals match the per-title rglob it replaces, for every
    title `iter_synced_titles()` yields.
    """

    def test_returns_total_bytes_per_title(self, patch_paths):
        sizes = synced_title_sizes()
        # Fixture pre-synced "After Life (2019) {tvdb-2}" with non-zero file.
        assert "After Life (2019) {tvdb-2}" in sizes
        assert sizes["After Life (2019) {tvdb-2}"] > 0

    def test_parity_with_per_title_rglob(self, patch_paths):
        """The exact algorithm /api/synced used pre-refactor: rglob('*'),
        stat each file, sum sizes. Computed inline here so any drift
        between the single-walk and the per-title walk surfaces as a
        diff in totals. This pins the perf optimization to NOT change
        the visible byte count for any synced title."""
        single_walk = synced_title_sizes()

        per_title: dict[str, int] = {}
        for _sub, title_dir in iter_synced_titles():
            total = 0
            for f in title_dir.rglob("*"):
                if f.is_file():
                    with contextlib.suppress(OSError):
                        total += f.stat().st_size
            per_title[title_dir.name] = total

        assert single_walk == per_title

    def test_dotfile_directories_excluded(self, patch_paths):
        """Hidden directories at the top level (e.g. .stversions from
        Syncthing) must not be reported as titles."""
        sync = patch_paths["sync"]
        (sync / "tv" / ".stversions").mkdir(exist_ok=True)
        (sync / "tv" / ".stversions" / "junk.mkv").write_bytes(b"x" * 100)
        sizes = synced_title_sizes()
        assert ".stversions" not in sizes

    def test_missing_sync_root_returns_empty(self, monkeypatch, tmp_path):
        monkeypatch.setattr("synclet.fs_helpers.SYNC_ROOT", tmp_path / "nope")
        assert synced_title_sizes() == {}
