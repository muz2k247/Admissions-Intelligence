"""Tests for extraction/review_gate.py -- the Phase Q confidence gate that
decides whether a record auto-publishes or is withheld for curator review.

Pure functions only -- no I/O, no Firestore, no network.
"""
from __future__ import annotations

from extraction.schema import DegreeLevel, ExtractedRecord, Field, NULL_FIELD
from extraction.review_gate import content_hash, flagged_fields, needs_review


def _record(**overrides):
    base = dict(
        institution_id="giki",
        campus=None,
        source_url="https://giki.edu.pk/admissions/",
        fetched_at="2026-07-09T00:00:00Z",
        chunk_id="giki",
        degree_level=DegreeLevel(value="Undergraduate"),
        constituent_college=NULL_FIELD,
        deadline=NULL_FIELD,
        programs=NULL_FIELD,
        admissions_open=NULL_FIELD,
    )
    base.update(overrides)
    return ExtractedRecord(**base)


# ---------------------------------------------------------------------------
# flagged_fields / needs_review
# ---------------------------------------------------------------------------

class TestFlaggedFields:
    def test_all_null_fields_are_never_flagged(self):
        record = _record()
        assert flagged_fields(record) == []
        assert needs_review(record) is False

    def test_high_confidence_value_is_not_flagged(self):
        record = _record(deadline=Field(value="2026-08-15", confidence=0.85))
        assert flagged_fields(record) == []
        assert needs_review(record) is False

    def test_value_at_threshold_boundary_is_not_flagged(self):
        # confidence == threshold is not "below" it.
        record = _record(deadline=Field(value="2026-08-15", confidence=0.8))
        assert flagged_fields(record, threshold=0.8) == []

    def test_value_below_threshold_is_flagged(self):
        record = _record(deadline=Field(value="2026-08-15", confidence=0.6))
        assert flagged_fields(record) == ["deadline"]
        assert needs_review(record) is True

    def test_null_field_never_flagged_even_though_confidence_is_none(self):
        # A null value has no confidence by the Field invariant -- this must
        # never be mistaken for a low-confidence guess (hard rule 1).
        record = _record(deadline=NULL_FIELD, programs=Field(value=["BS CS"], confidence=0.9))
        assert flagged_fields(record) == []

    def test_multiple_fields_flagged_independently(self):
        # A strong deadline field must not excuse a weak programs field.
        record = _record(
            deadline=Field(value="2026-08-15", confidence=0.95),
            programs=Field(value=["BS CS"], confidence=0.4),
            admissions_open=Field(value="Open", confidence=0.5),
        )
        assert flagged_fields(record) == ["programs", "admissions_open"]
        assert needs_review(record) is True

    def test_flagged_fields_order_matches_review_fields(self):
        record = _record(
            admissions_open=Field(value="Open", confidence=0.3),
            deadline=Field(value="2026-08-15", confidence=0.3),
        )
        assert flagged_fields(record) == ["deadline", "admissions_open"]

    def test_custom_threshold_is_respected(self):
        record = _record(deadline=Field(value="2026-08-15", confidence=0.6))
        assert flagged_fields(record, threshold=0.5) == []
        assert flagged_fields(record, threshold=0.7) == ["deadline"]

    def test_constituent_college_is_a_review_field(self):
        record = _record(constituent_college=Field(value="Allied", confidence=0.3))
        assert flagged_fields(record) == ["constituent_college"]

    def test_degree_level_is_never_part_of_the_gate(self):
        # degree_level is a classifier decision, not curator-overridable, and
        # not part of REVIEW_FIELDS -- an Ambiguous record with otherwise
        # high-confidence fields must not be flagged by this gate.
        record = _record(
            degree_level=DegreeLevel(value=None, reason="no-signal"),
            deadline=Field(value="2026-08-15", confidence=0.95),
        )
        assert flagged_fields(record) == []


# ---------------------------------------------------------------------------
# content_hash
# ---------------------------------------------------------------------------

class TestContentHash:
    def test_hash_is_deterministic(self):
        record = _record(deadline=Field(value="2026-08-15", confidence=0.6))
        assert content_hash(record) == content_hash(record)

    def test_hash_is_64_char_hex(self):
        record = _record()
        h = content_hash(record)
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_hash_differs_when_a_reviewable_field_changes(self):
        r1 = _record(deadline=Field(value="2026-08-15", confidence=0.6))
        r2 = _record(deadline=Field(value="2026-09-01", confidence=0.6))
        assert content_hash(r1) != content_hash(r2)

    def test_hash_ignores_non_reviewable_attributes(self):
        # confidence/note/source_url/fetched_at are not part of the hash --
        # only the four reviewable field VALUES are, so re-extracting the
        # same content with a different confidence score doesn't churn it.
        r1 = _record(deadline=Field(value="2026-08-15", confidence=0.6))
        r2 = _record(
            deadline=Field(value="2026-08-15", confidence=0.99, note="llm"),
            fetched_at="2026-07-20T00:00:00Z",
            source_url="https://giki.edu.pk/admissions/other",
        )
        assert content_hash(r1) == content_hash(r2)

    def test_hash_all_null_fields_is_stable(self):
        record = _record()
        assert content_hash(record) == content_hash(_record())

    def test_hash_distinguishes_programs_list_order(self):
        r1 = _record(programs=Field(value=["BS CS", "BE EE"], confidence=0.9))
        r2 = _record(programs=Field(value=["BE EE", "BS CS"], confidence=0.9))
        assert content_hash(r1) != content_hash(r2)

    def test_hash_handles_multi_entry_deadline_shape(self):
        # deadline can be a list[{"label", "date"}] for genuinely multiple
        # distinct deadlines (extraction/schema.py) -- must hash without
        # raising and stay deterministic/order-sensitive like any other list.
        multi = [{"label": "Engineering", "date": "2026-08-15"}, {"label": "CS", "date": "2026-08-20"}]
        r1 = _record(deadline=Field(value=multi, confidence=0.9))
        r2 = _record(deadline=Field(value=multi, confidence=0.9))
        reordered = _record(deadline=Field(value=list(reversed(multi)), confidence=0.9))
        assert content_hash(r1) == content_hash(r2)
        assert content_hash(r1) != content_hash(reordered)

    def test_hash_does_not_escape_non_ascii_characters(self):
        # ensure_ascii=False is required for parity with JS's JSON.stringify,
        # which does not escape non-ASCII -- a differently-encoded canonical
        # string would silently break the Python/JS hash match.
        record = _record(constituent_college=Field(value="Allāma Iqbal Medical College", confidence=0.6))
        import hashlib
        import json
        expected_canonical = json.dumps(
            [None, None, "Allāma Iqbal Medical College", None],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        expected = hashlib.sha256(expected_canonical.encode("utf-8")).hexdigest()
        assert content_hash(record) == expected
        # sanity: the ensure_ascii=True encoding would have produced a
        # different digest, proving this test actually exercises the flag.
        ascii_canonical = json.dumps(
            [None, None, "Allāma Iqbal Medical College", None],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        assert hashlib.sha256(ascii_canonical.encode("utf-8")).hexdigest() != expected

    def test_hash_matches_known_fixture_value(self):
        # Fixed fixture used for cross-language parity with the JS
        # implementation in dashboard/admin (chunk 5) -- both sides must
        # produce this exact digest for this exact record.
        record = _record(
            deadline=Field(value="2026-08-15", confidence=0.6),
            programs=Field(value=["BS CS", "BS EE"], confidence=0.9),
            admissions_open=Field(value="Open", confidence=0.8),
            constituent_college=NULL_FIELD,
        )
        import hashlib
        import json
        expected_canonical = json.dumps(
            ["2026-08-15", ["BS CS", "BS EE"], None, "Open"],
            sort_keys=True,
            separators=(",", ":"),
        )
        expected = hashlib.sha256(expected_canonical.encode("utf-8")).hexdigest()
        assert content_hash(record) == expected


# ---------------------------------------------------------------------------
# Additional coverage: threshold boundaries, Field invariant defensiveness,
# unicode/list-shaped values, and hash edge cases (QA follow-up).
# ---------------------------------------------------------------------------

class TestThresholdBoundaries:
    def test_zero_confidence_at_zero_threshold_is_not_flagged(self):
        # confidence == threshold is "not below" it, even at the 0.0 extreme.
        record = _record(deadline=Field(value="2026-08-15", confidence=0.0))
        assert flagged_fields(record, threshold=0.0) == []

    def test_zero_confidence_above_zero_threshold_is_flagged(self):
        record = _record(deadline=Field(value="2026-08-15", confidence=0.0))
        assert flagged_fields(record, threshold=0.01) == ["deadline"]

    def test_full_confidence_at_one_threshold_is_not_flagged(self):
        record = _record(deadline=Field(value="2026-08-15", confidence=1.0))
        assert flagged_fields(record, threshold=1.0) == []

    def test_just_below_full_confidence_is_flagged_at_one_threshold(self):
        record = _record(deadline=Field(value="2026-08-15", confidence=0.999))
        assert flagged_fields(record, threshold=1.0) == ["deadline"]

    def test_confidence_just_below_default_threshold_is_flagged(self):
        record = _record(deadline=Field(value="2026-08-15", confidence=0.7999999))
        assert flagged_fields(record) == ["deadline"]


class TestFieldInvariantDefensiveness:
    # The Field dataclass's __post_init__ forbids constructing a non-null
    # value with confidence=None through the normal API, so flagged_fields'
    # defensive `field.confidence is None` branch (review_gate.py line 37)
    # is unreachable via Field(...). We reach it anyway by mutating a frozen
    # instance in place after construction -- this is the only way to prove
    # the defensive branch actually flags rather than crashing or silently
    # skipping, should the invariant ever be violated upstream.
    def test_value_with_none_confidence_is_defensively_flagged(self):
        record = _record(deadline=Field(value="2026-08-15", confidence=0.95))
        object.__setattr__(record.deadline, "confidence", None)
        assert flagged_fields(record) == ["deadline"]
        assert needs_review(record) is True


class TestUnicodeAndListShapedValues:
    def test_programs_list_with_non_ascii_entries_flagged_correctly(self):
        record = _record(programs=Field(value=["BS الحاسوب", "BE EE"], confidence=0.5))
        assert flagged_fields(record) == ["programs"]

    def test_empty_list_value_is_non_null_and_evaluated_normally(self):
        # An empty list is a non-null value (Field only checks `is None`),
        # so a low-confidence empty-list extraction is still flag-worthy --
        # this must not be silently treated as "nothing extracted".
        record = _record(programs=Field(value=[], confidence=0.5))
        assert flagged_fields(record) == ["programs"]

    def test_empty_list_value_at_high_confidence_is_not_flagged(self):
        record = _record(programs=Field(value=[], confidence=0.9))
        assert flagged_fields(record) == []


class TestHashEdgeCases:
    def test_hash_distinguishes_empty_list_from_null(self):
        r_null = _record()
        r_empty = _record(programs=Field(value=[], confidence=0.9))
        assert content_hash(r_null) != content_hash(r_empty)

    def test_hash_distinguishes_similar_but_different_dict_keys_in_deadline(self):
        # Confirms dict-key sorting within a multi-entry deadline doesn't
        # accidentally make semantically different entries collide.
        r1 = _record(deadline=Field(value=[{"label": "CS", "date": "2026-08-15"}], confidence=0.9))
        r2 = _record(deadline=Field(value=[{"label": "2026-08-15", "date": "CS"}], confidence=0.9))
        assert content_hash(r1) != content_hash(r2)

    def test_hash_is_stable_across_repeated_calls_on_new_equal_instances(self):
        # Determinism must hold across independently-constructed records with
        # equal (not identical) field values, not just repeated calls on the
        # same object -- guards against any accidental reliance on object
        # identity or insertion-order-dependent hashing (e.g. via id()).
        r1 = _record(
            deadline=Field(value="2026-08-15", confidence=0.6),
            constituent_college=Field(value="Nishtar Medical University", confidence=0.7),
        )
        r2 = _record(
            deadline=Field(value="2026-08-15", confidence=0.6),
            constituent_college=Field(value="Nishtar Medical University", confidence=0.7),
        )
        assert content_hash(r1) == content_hash(r2)

    def test_hash_non_ascii_in_programs_list(self):
        record = _record(programs=Field(value=["بی ایس سی ایےس"], confidence=0.9))
        h = content_hash(record)
        assert len(h) == 64
        # ensure_ascii=False path taken: raw non-ascii bytes hash differently
        # than the ascii-escaped equivalent would.
        import hashlib
        import json
        ascii_canonical = json.dumps(
            [None, ["بی ایس سی ایےس"], None, None],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        assert h != hashlib.sha256(ascii_canonical.encode("utf-8")).hexdigest()
