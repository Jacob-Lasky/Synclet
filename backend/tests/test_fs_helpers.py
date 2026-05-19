"""Verify the dedup logic in iter_sync_subs (tv + tv-4kUHD share `tv` sub)."""

from synclet.fs_helpers import iter_sync_subs, iter_synced_titles


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
