"""Tests for synclet.scan pure helpers + filesystem walkers.

Pure-function tests use no fixtures. Walker tests use the `media_tree` and
`patch_paths` fixtures so we exercise the real filesystem code against a
synthetic tree.
"""

from pathlib import Path

from synclet.scan import (
    clean_name,
    folder_external_ids,
    is_wanted_file,
    normalized_key,
    parse_year,
    scan_title_detail,
    scan_titles,
    title_detail_to_dict,
    watchstate_key,
)


class TestCleanName:
    def test_strips_tvdb(self):
        assert clean_name("Severance {tvdb-371980}") == "Severance"

    def test_strips_tmdb(self):
        assert clean_name("1917 (2019) {tmdb-530915}") == "1917 (2019)"

    def test_strips_imdb_with_tt_prefix(self):
        # imdb ids are tt-prefixed; the cruft stripper must handle them too or
        # the title key keeps the suffix and misses Plex/WatchState.
        assert clean_name("Nobody (2021) {imdb-tt3530002}") == "Nobody (2021)"

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

    def test_strips_imdb(self):
        assert watchstate_key("Nobody (2021) {imdb-tt3530002}") == "nobody"

    def test_already_clean(self):
        assert watchstate_key("Fallout") == "fallout"

    def test_lowercases(self):
        assert watchstate_key("BETTER CALL SAUL") == "better call saul"


class TestFolderExternalIds:
    def test_extracts_tvdb(self):
        assert folder_external_ids("Pluribus (2025) {tvdb-436457}") == {
            "tvdb": "436457"
        }

    def test_extracts_tmdb(self):
        assert folder_external_ids("Se7en (1995) {tmdb-807}") == {"tmdb": "807"}

    def test_extracts_imdb_with_tt_prefix(self):
        # imdb ids keep their tt prefix so they match Plex's imdb://tt... Guid.
        assert folder_external_ids("Nobody {imdb-tt3530002}") == {"imdb": "tt3530002"}

    def test_none_when_no_ids(self):
        # YouTube channel folders carry no external id.
        assert folder_external_ids("Hank Green") == {}


class TestNormalizedKey:
    def test_strips_punctuation(self):
        # Plex drops the hyphen this folder keeps; both must collapse to one key.
        assert (
            normalized_key("Complexly - Ask Hank Anything")
            == "complexlyaskhankanything"
        )
        assert (
            normalized_key("Complexly Ask Hank Anything") == "complexlyaskhankanything"
        )

    def test_strips_year_tvdb_and_case(self):
        assert normalized_key("Better Call Saul (2015) {tvdb-1}") == "bettercallsaul"


class TestIsWantedFile:
    def test_video_kept(self):
        assert is_wanted_file(Path("Show.S01E01.mkv"))

    def test_english_sub_kept(self):
        assert is_wanted_file(Path("Show.S01E01.en.srt"))
        assert is_wanted_file(Path("Show.S01E01.eng.srt"))

    def test_foreign_sub_dropped(self):
        assert not is_wanted_file(Path("Show.S01E01.fr.srt"))
        assert not is_wanted_file(Path("Show.S01E01.de.srt"))

    def test_forced_sub_follows_its_language(self):
        # "forced" tags the track kind; the language decides, as for any sub.
        assert is_wanted_file(Path("Show.S01E01.en.forced.srt"))
        assert is_wanted_file(Path("Show.S01E01.forced.en.srt"))
        assert not is_wanted_file(Path("Show.S01E01.fr.forced.srt"))

    def test_sub_with_no_lang_kept(self):
        # Bare "Show.srt" with no detectable lang code → keep
        assert is_wanted_file(Path("Show.srt"))

    def test_lone_hi_is_hindi_and_dropped(self):
        # "hi" is ISO 639-1 Hindi. With no language code before it, it names
        # the subtitle's language, not a hearing-impaired English track.
        assert not is_wanted_file(Path("Show.S01E01.hi.srt"))
        assert not is_wanted_file(
            Path(
                "Black Hawk Down (2001) {tmdb-855} - [WEBDL-1080p][EAC3 5.1]"
                "[h265]-NINJACENTRAL.hi.srt"
            )
        )

    def test_hi_after_a_language_is_hearing_impaired(self):
        assert is_wanted_file(Path("Show.S01E01.en.hi.srt"))
        assert is_wanted_file(Path("Show.S01E01.eng.hi.srt"))
        assert is_wanted_file(Path("Show.S01E01.en.forced.hi.srt"))
        assert is_wanted_file(Path("Show.S01E01.en.hi.forced.srt"))
        assert not is_wanted_file(Path("Show.S01E01.fr.hi.srt"))

    def test_region_tagged_languages(self):
        assert is_wanted_file(Path("Movie.en-US.srt"))
        assert is_wanted_file(Path("Movie.en-US.hi.srt"))
        assert not is_wanted_file(Path("Movie.fr-FR.srt"))
        assert not is_wanted_file(Path("Movie.pt-BR.forced.srt"))
        assert not is_wanted_file(Path("Movie.zh-Hant-TW.srt"))
        assert not is_wanted_file(Path("Movie.en.fr-FR.forced.srt"))

    def test_copy_number_is_not_a_language(self):
        # Sonarr/Radarr number duplicate subs before the language code.
        assert is_wanted_file(Path("Show.S01E01.1.en.srt"))
        assert not is_wanted_file(Path("Show.S01E01.2.fr.srt"))
        assert not is_wanted_file(Path("Show.S01E01.fr.2.srt"))
        assert is_wanted_file(Path("Show.S01E01.2.srt"))

    def test_release_tokens_are_not_languages(self):
        assert is_wanted_file(Path("Movie.2024.WEB-DL.srt"))
        assert is_wanted_file(Path("Movie.2024.1080p.BluRay.x264-GRP.srt"))

    def test_video_name_is_never_read_as_a_language(self):
        # Only the token after the video name carries the language; a short
        # word inside the name is not one.
        assert is_wanted_file(Path("Mr.Robot.S01E01.srt"))
        assert is_wanted_file(Path("Ted.srt"))
        assert is_wanted_file(Path("Ted.forced.srt"))
        assert not is_wanted_file(Path("Ted.hi.srt"))

    def test_hindi_kept_when_hindi_is_wanted(self, monkeypatch):
        from synclet import scan

        monkeypatch.setattr(scan, "ENGLISH_CODES", {"en", "eng", "hi"})
        assert is_wanted_file(Path("Show.S01E01.hi.srt"))

    def test_unambiguous_qualifiers_alone_carry_no_language(self):
        # Every qualifier but hi is not an ISO 639-1/639-2 code. foreign and
        # psdh are Sonarr/Radarr tags: a French sub tagged with one is French.
        for q in ("sdh", "cc", "forced", "foreign", "psdh", "default", "full"):
            assert is_wanted_file(Path(f"Show.S01E01.{q}.srt")), q
            assert is_wanted_file(Path(f"Show.S01E01.en.{q}.srt")), q
            assert not is_wanted_file(Path(f"Show.S01E01.fr.{q}.srt")), q


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

    def test_episode_wire_marks_subtitle_only_row_without_video(self, patch_paths):
        folder = "Better Call Saul (2015) {tvdb-1}"
        season = patch_paths["media"] / "tv" / folder / "Season 01"
        (season / "Better Call Saul - S01E03 - Third.en.srt").write_bytes(b"sub")

        detail = scan_title_detail("tv", folder)
        assert detail is not None
        episodes = title_detail_to_dict(detail)["seasons"][0]["episodes"]
        by_number = {episode["episode"]: episode for episode in episodes}
        assert by_number[1].get("has_video") is True
        assert by_number[3].get("has_video") is False

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


def test_image_subtitles_get_the_english_filter():
    from pathlib import Path

    from synclet.scan import is_wanted_file

    assert is_wanted_file(Path("Film.en.sup"))
    assert is_wanted_file(Path("Film.eng.forced.idx"))
    assert not is_wanted_file(Path("Film.fre.sup"))
    assert not is_wanted_file(Path("Film.ger.idx"))
