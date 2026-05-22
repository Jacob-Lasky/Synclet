"""fuzzy_score is the canonical title-matching algorithm shared by watchlist
and resolve. Behavior is duplicated on the frontend (store.ts:fuzzyScore) ,
if either side changes, both should change.
"""

import pytest

from synclet.fuzzy import fuzzy_score


class TestFuzzyScore:
    def test_exact_match(self):
        assert fuzzy_score("Fallout", "Fallout") == pytest.approx(2.0)

    def test_case_insensitive_exact(self):
        assert fuzzy_score("fallout", "FALLOUT") == pytest.approx(2.0)

    def test_substring_match(self):
        # "fa" appears in "Fallout"
        score = fuzzy_score("fa", "Fallout")
        assert 1.0 < score < 2.0

    def test_substring_shorter_target_scores_higher(self):
        # "fall" matches "Fall" tighter than "Fallout"
        assert fuzzy_score("fall", "Fall") > fuzzy_score("fall", "Fallout")

    def test_all_words_substring(self):
        # Each word appears in target, but not as contiguous substring
        score = fuzzy_score("call saul better", "Better Call Saul")
        assert score == pytest.approx(0.9)

    def test_prefix_match(self):
        # "fa" matches the first word of "Fallout Series"
        score = fuzzy_score("fa", "Fallout Series")
        # Substring wins over prefix: "fa" is in "Fallout Series"
        assert score > 0.5

    def test_no_match(self):
        # SequenceMatcher fallback yields a small score; must be below the
        # threshold the resolve.py uses (_FUZZY_MIN_SCORE = 0.6).
        assert fuzzy_score("xyz", "Fallout") < 0.4

    def test_empty_query(self):
        assert fuzzy_score("", "Fallout") == pytest.approx(0.0)

    def test_ordering_consistency(self):
        # Verify ranking matches user intuition: more specific query ranks
        # the precise target higher than a partial one.
        scores = [
            ("better call saul", "Better Call Saul"),  # exact
            ("better call", "Better Call Saul"),  # substring
            ("call", "Better Call Saul"),  # substring
            ("xyz", "Better Call Saul"),  # no match
        ]
        results = [fuzzy_score(q, t) for q, t in scores]
        assert results == sorted(results, reverse=True), f"Not monotonic: {results}"
