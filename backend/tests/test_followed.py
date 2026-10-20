"""Following is persisted intent, so reading it does not probe every source path."""

from contextlib import nullcontext

from synclet import followed, library


def test_followed_read_does_not_revalidate_unrelated_source_folders(
    patch_paths, monkeypatch
):
    folder = "After Life (2019) {tvdb-2}"
    followed.set_following("tv", folder, True)

    def unavailable_source(*args):
        raise AssertionError("source directory was probed during followed read")

    monkeypatch.setattr(followed, "_valid_title", unavailable_source)

    assert ("tv", folder) in followed.get_followed()


def test_one_unreadable_source_entry_does_not_hide_other_follows(
    patch_paths, monkeypatch
):
    folder = "After Life (2019) {tvdb-2}"
    followed.set_following("tv", folder, True)
    library.reset()
    source_lib = patch_paths["media"] / "tv"
    original_scandir = library.os.scandir

    class UnreadableEntry:
        name = "vanished"

        def is_dir(self, *, follow_symlinks):
            raise OSError("entry vanished")

    def flaky_scandir(path):
        if path == source_lib:
            with original_scandir(path) as entries:
                return nullcontext(iter([UnreadableEntry(), *list(entries)]))
        return original_scandir(path)

    monkeypatch.setattr(library.os, "scandir", flaky_scandir)
    library.folders("tv")

    assert ("tv", folder) in followed.get_display_followed()


def test_display_hides_a_title_the_listing_says_is_gone(patch_paths):
    folder = "After Life (2019) {tvdb-2}"
    followed.set_following("tv", folder, True)
    library.folders("tv")
    title = patch_paths["media"] / "tv" / folder
    for path in sorted(title.rglob("*"), reverse=True):
        path.unlink() if path.is_file() else path.rmdir()
    title.rmdir()
    library.note_title("tv", folder)

    assert ("tv", folder) not in followed.get_display_followed()
    # Intent is kept, so the title returns if the folder does.
    assert ("tv", folder) in followed.get_followed()


def test_display_reads_no_listing_on_the_request_path(patch_paths, monkeypatch):
    folder = "After Life (2019) {tvdb-2}"
    followed.set_following("tv", folder, True)
    library.reset()
    monkeypatch.setattr(
        library, "_list_library", lambda lib: (_ for _ in ()).throw(AssertionError)
    )
    assert ("tv", folder) in followed.get_display_followed()


def test_follow_and_unfollow_flag_the_new_episode_badges(patch_paths):
    from synclet import maint_cache, synced

    # Not synced in the fixture, so the upgrade bootstrap has not followed it.
    folder = "Better Call Saul (2015) {tvdb-1}"
    for following in (True, False):
        maint_cache.clear()
        synced.register_builders()
        followed.set_following("tv", folder, following)
        assert maint_cache.is_dirty("synced_enrichment")
