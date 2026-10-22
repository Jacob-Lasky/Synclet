"""The shared episode-number parser (synclet.episodes)."""

from synclet.episodes import EPISODE_PAT, episode_numbers


def test_episode_numbers_cover_multi_episode_names():
    assert episode_numbers("Show - S01E01E02.mkv") == {(1, 1), (1, 2)}
    assert episode_numbers("Show - S01E01-E02 - x.mkv") == {(1, 1), (1, 2)}
    assert episode_numbers("Show - s00e05.mkv") == {(0, 5)}
    assert episode_numbers("Show - 1080p.mkv") == set()


def test_episode_pat_takes_the_first_episode():
    match = EPISODE_PAT.search("Show - S02E07E08.mkv")
    assert match is not None
    assert (match.group(1), match.group(2)) == ("02", "07")
