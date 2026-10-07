"""Following is persisted intent, so reading it does not probe every source path."""

from contextlib import nullcontext

from synclet import followed


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
    source_lib = patch_paths["media"] / "tv"
    original_scandir = followed.os.scandir

    class UnreadableEntry:
        name = "vanished"

        def is_dir(self, *, follow_symlinks):
            raise OSError("entry vanished")

    def flaky_scandir(path):
        if path == source_lib:
            with original_scandir(path) as entries:
                return nullcontext(iter([UnreadableEntry(), *list(entries)]))
        return original_scandir(path)

    monkeypatch.setattr(followed.os, "scandir", flaky_scandir)
    presence = followed._build_source_presence()

    assert presence["tv", folder][1] is True
