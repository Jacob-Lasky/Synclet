"""Tests for synclet.scan pure helpers + filesystem walkers.

Pure-function tests use no fixtures. Walker tests use the `media_tree` and
`patch_paths` fixtures so we exercise the real filesystem code against a
synthetic tree.
"""

from pathlib import Path

from synclet.scan import (
    clean_name,
    is_wanted_file,
    parse_year,
    scan_title_detail,
    scan_titles,
    watchstate_key,
)


class TestCleanName:
    def test_strips_tvdb(self):
        assert clean_name("Severance {tvdb-371980}") == "Severance"

    def test_strips_tmdb(self):
        assert clean_name("1917 (2019) {tmdb-530915}") == "1917 (2019)"

    def test_keeps_year(self):
        assert (
            clean_name("Better Call Saul (2015) {tvdb-1}") == "Better Call Saul (2015)"
        )

    def test_no_cruft(self):
        assert clean_name("Fallout") == "Fallout"


class TestParseYear:
    def test_finds_year(self):
        assert parse_year("Better Call Saul (2015) {tvdb-1}") == 2015

    def test_year_with_tvdb_stripped(self):
        assert parse_year("1917 (2019) {tmdb-530915}") == 2019

    def test_no_year(self):
        assert parse_year("Fallout") is None

    def test_only_year_at_end(self):
        # 4-digit number not in parens should not be parsed as year
        assert parse_year("Show 2025 Behind The Scenes") is None


class TestWatchstateKey:
    def test_strips_year_and_tvdb(self):
        assert watchstate_key("Better Call Saul (2015) {tvdb-1}") == "better call saul"

    def test_strips_tmdb(self):
        assert watchstate_key("1917 (2019) {tmdb-530915}") == "1917"

    def test_already_clean(self):
        assert watchstate_key("Fallout") == "fallout"

    def test_lowercases(self):
        assert watchstate_key("BETTER CALL SAUL") == "better call saul"


class TestIsWantedFile:
    def test_video_kept(self):
        assert is_wanted_file(Path("Show.S01E01.mkv"))

    def test_english_sub_kept(self):
        assert is_wanted_file(Path("Show.S01E01.en.srt"))
        assert is_wanted_file(Path("Show.S01E01.eng.srt"))

    def test_foreign_sub_dropped(self):
        assert not is_wanted_file(Path("Show.S01E01.fr.srt"))
        assert not is_wanted_file(Path("Show.S01E01.de.srt"))

    def test_forced_sub_kept(self):
        # "forced" qualifier means it shows during non-English audio , always wanted
        # The lang code is the last 2-3 letter token NOT in SUBTITLE_QUALIFIERS
        assert is_wanted_file(Path("Show.S01E01.forced.en.srt"))

    def test_sub_with_no_lang_kept(self):
        # Bare "Show.srt" with no detectable lang code → keep
        assert is_wanted_file(Path("Show.srt"))


class TestScanTitles:
    def test_finds_titles(self, patch_paths, patch_watchstate):
        titles = scan_titles()
        names = {t.name for t in titles}
        assert "Better Call Saul (2015)" in names
        assert "After Life (2019)" in names
        assert "1917 (2019)" in names

    def test_kind_is_classified(self, patch_paths, patch_watchstate):
        titles = scan_titles()
        kinds = {t.name: t.kind for t in titles}
        assert kinds["Better Call Saul (2015)"] == "show"
        assert kinds["1917 (2019)"] == "movie"

    def test_synced_count_detected(self, patch_paths, patch_watchstate):
        titles = scan_titles()
        al = next(t for t in titles if t.name == "After Life (2019)")
        # After Life has one video pre-synced in the fixture tree
        assert al.has_synced is True
        assert al.synced_files == 1


class TestScanTitleDetail:
    def test_returns_none_for_unknown_lib(self, patch_paths):
        assert scan_title_detail("nonexistent", "anything") is None

    def test_returns_none_for_missing_folder(self, patch_paths):
        assert scan_title_detail("tv", "Does Not Exist") is None

    def test_show_detail(self, patch_paths):
        d = scan_title_detail("tv", "Better Call Saul (2015) {tvdb-1}")
        assert d is not None
        assert d.kind == "show"
        assert len(d.seasons) == 1
        season1 = d.seasons[0]
        assert season1.season == 1
        assert len(season1.episodes) == 2
        # English sub should be counted; French sub should not show up in files
        e1 = next(e for e in season1.episodes if e.episode == 1)
        assert all("fr.srt" not in f for f in e1.files), (
            f"French subtitle leaked through filter: {e1.files}"
        )

    def test_episode_synced_despite_reencode_rename(self, patch_paths):
        """A source-side re-encode renames the file but keeps the same SxxExx.

        Regression: the detail view used to check the exact synced path, so an
        x264 -> h265 source upgrade made every episode read as un-synced even
        though the synced folder still held it. Match on SxxExx instead.
        """
        media = patch_paths["media"]
        sync = patch_paths["sync"]

        src = media / "tv" / "Reencode Show (2020) {tvdb-9}" / "Season 01"
        src.mkdir(parents=True)
        # Source got upgraded to h265 after the sync happened.
        (src / "Reencode Show - S01E01 - Pilot [WEBDL-1080p][h265].mkv").write_bytes(
            b"\0" * 1024
        )
        # S01E02 exists in source but was never synced.
        (src / "Reencode Show - S01E02 - Two [WEBDL-1080p][h265].mkv").write_bytes(
            b"\0" * 1024
        )

        syn = sync / "tv" / "Reencode Show (2020) {tvdb-9}" / "Season 01"
        syn.mkdir(parents=True)
        # Synced copy is the old x264 encode , different filename, same episode.
        (syn / "Reencode Show - S01E01 - Pilot [WEBDL-1080p][x264].mkv").write_bytes(
            b"\0" * 1024
        )

        d = scan_title_detail("tv", "Reencode Show (2020) {tvdb-9}")
        assert d is not None
        eps = {e.episode: e for e in d.seasons[0].episodes}
        assert eps[1].is_synced is True, "re-encoded episode should match on SxxExx"
        assert eps[2].is_synced is False, "un-synced episode must not be marked synced"

    def test_movie_synced_despite_reencode_rename(self, patch_paths):
        """A re-encoded movie keeps its title folder but renames the video.

        Regression: the movie detail used to mark each file by exact synced
        path, so an x264 -> h265 source upgrade showed the movie as un-synced.
        Match on the title folder holding a video instead.
        """
        media = patch_paths["media"]
        sync = patch_paths["sync"]

        src = media / "movies" / "Reencode Movie (2018) {tmdb-9}"
        src.mkdir(parents=True)
        (src / "Reencode Movie (2018) [Bluray-1080p][h265].mkv").write_bytes(
            b"\0" * 2048
        )

        syn = sync / "movies" / "Reencode Movie (2018) {tmdb-9}"
        syn.mkdir(parents=True)
        # Synced copy is the old x264 encode , different filename, same movie.
        (syn / "Reencode Movie (2018) [Bluray-1080p][x264].mkv").write_bytes(
            b"\0" * 2048
        )

        d = scan_title_detail("movies", "Reencode Movie (2018) {tmdb-9}")
        assert d is not None
        assert d.synced_bytes > 0, "re-encoded movie should read as synced"
        assert all(f["is_synced"] for f in d.files)

    def test_movie_unsynced_when_no_synced_video(self, patch_paths):
        """1917 has a source video but no synced copy , must read un-synced."""
        d = scan_title_detail("movies", "1917 (2019) {tmdb-3}")
        assert d is not None
        assert d.synced_bytes == 0
        assert not any(f["is_synced"] for f in d.files)

    def test_movie_detail(self, patch_paths):
        d = scan_title_detail("movies", "1917 (2019) {tmdb-3}")
        assert d is not None
        assert d.kind == "movie"
        assert len(d.seasons) == 0
        names = [f["name"] for f in d.files]
        assert "1917.mkv" in names
        # Foreign sub filtered
        assert "1917.fr.srt" not in names
        # English sub kept
        assert "1917.en.srt" in names
