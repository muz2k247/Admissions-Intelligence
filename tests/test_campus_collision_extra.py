"""Additional campus-collision coverage, written to fill gaps not already
exercised by tests/test_scraper.py's TestConfig campus-collision block or
tests/test_institutions_registry.py's TestCampusCollisionDegradesGracefully:

1. A collision positioned between the 2nd and 3rd source of a 3+-source
   institution (not the 1st and 2nd) -- find_campus_collision must scan the
   whole list, not just adjacent pairs or the first two entries.
2. A campus value that is an actual empty string "" (as distinct from None
   or a whitespace-only string) bucketing with None/whitespace-only, both at
   the campus_collision_key level (already covered elsewhere) and threaded
   through a real multi-source Source list / YAML load / Firestore doc.
3. pipeline/institutions_registry.py's _build_institution rejecting a
   Firestore doc whole when the collision is between two OTHERWISE-VALID
   sources (good url/format/render each) sitting alongside a first source
   that is also valid -- confirming the fallback to the YAML version is
   real fallback (the pre-existing good YAML institution, sources intact),
   not a partial/first-source-only application of the bad doc.
"""
from __future__ import annotations

import pytest

from pipeline.institutions_registry import merge_institutions
from scraper.config import Institution, Source, find_campus_collision, load_institutions


# ---------------------------------------------------------------------------
# 1. Collision at position 2-3 of a 3+-source institution
# ---------------------------------------------------------------------------

class TestCollisionNotAtFirstPair:
    def test_find_campus_collision_catches_collision_between_second_and_third_source(self):
        sources = [
            Source(institution_id="x", campus="Lahore", url="https://a.example", format="html"),
            Source(institution_id="x", campus="Karachi", url="https://b.example", format="html"),
            Source(institution_id="x", campus="Karachi", url="https://c.example", format="html"),
        ]
        collision = find_campus_collision(sources)
        assert collision is not None
        assert collision == ("Karachi", "Karachi")

    def test_find_campus_collision_catches_case_whitespace_variant_at_second_and_third(self):
        sources = [
            Source(institution_id="x", campus="Lahore", url="https://a.example", format="html"),
            Source(institution_id="x", campus="Islamabad", url="https://b.example", format="html"),
            Source(institution_id="x", campus="  ISLAMABAD  ", url="https://c.example", format="html"),
        ]
        collision = find_campus_collision(sources)
        # Label is trimmed (matching the JS port's behavior) -- the raw,
        # untrimmed "  ISLAMABAD  " would be a confusing/invisible-whitespace
        # error message.
        assert collision == ("Islamabad", "ISLAMABAD")

    def test_load_institutions_raises_for_three_source_institution_colliding_at_second_third(self, tmp_path):
        config_path = tmp_path / "institutions.yaml"
        config_path.write_text(
            """
institutions:
  - id: three_campus
    name: Three Campus University
    sources:
      - campus: "Lahore"
        url: "https://a.example.edu"
        format: html
      - campus: "Karachi"
        url: "https://b.example.edu"
        format: html
      - campus: "Karachi"
        url: "https://c.example.edu"
        format: html
""",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="collide"):
            load_institutions(config_path)

    def test_first_pair_distinct_does_not_mask_later_collision(self):
        # A naive implementation that only checked sources[0] vs sources[1]
        # would wrongly pass this. Four sources: 1-2 distinct, 3-4 collide.
        sources = [
            Source(institution_id="x", campus="A", url="https://a.example", format="html"),
            Source(institution_id="x", campus="B", url="https://b.example", format="html"),
            Source(institution_id="x", campus="C", url="https://c.example", format="html"),
            Source(institution_id="x", campus="C", url="https://d.example", format="html"),
        ]
        assert find_campus_collision(sources) == ("C", "C")


# ---------------------------------------------------------------------------
# 2. Empty string "" campus bucketing
# ---------------------------------------------------------------------------

class TestEmptyStringCampus:
    def test_find_campus_collision_buckets_empty_string_with_none(self):
        sources = [
            Source(institution_id="x", campus=None, url="https://a.example", format="html"),
            Source(institution_id="x", campus="", url="https://b.example", format="html"),
        ]
        collision = find_campus_collision(sources)
        assert collision is not None
        # label for empty-string campus falls back to "(no campus)" same as None,
        # since `src.campus or "(no campus)"` treats "" as falsy too.
        assert collision == ("(no campus)", "(no campus)")

    def test_find_campus_collision_buckets_empty_string_with_whitespace_only(self):
        sources = [
            Source(institution_id="x", campus="   ", url="https://a.example", format="html"),
            Source(institution_id="x", campus="", url="https://b.example", format="html"),
        ]
        assert find_campus_collision(sources) is not None

    def test_load_institutions_raises_for_empty_string_campus_colliding_with_null(self, tmp_path):
        config_path = tmp_path / "institutions.yaml"
        config_path.write_text(
            """
institutions:
  - id: bad
    name: Bad Institution
    sources:
      - campus: null
        url: "https://a.example.edu"
        format: html
      - campus: ""
        url: "https://b.example.edu"
        format: html
""",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="collide"):
            load_institutions(config_path)

    def test_empty_string_campus_does_not_collide_with_a_real_campus_name(self):
        # Sanity: "" only collides with other falsy/whitespace-only campuses,
        # not with any populated campus name.
        sources = [
            Source(institution_id="x", campus="", url="https://a.example", format="html"),
            Source(institution_id="x", campus="Lahore", url="https://b.example", format="html"),
        ]
        assert find_campus_collision(sources) is None


# ---------------------------------------------------------------------------
# 3. pipeline/institutions_registry.py: whole-doc rejection with real fallback,
#    not partial/first-source-only application
# ---------------------------------------------------------------------------

class TestFirestoreCollisionWholeDocRejectionWithRealFallback:
    def test_three_valid_sources_colliding_second_third_rejects_whole_doc_falls_back_to_yaml(self):
        yaml_good = Institution(
            id="multi",
            name="Multi Campus University (yaml, good)",
            admitting_body=False,
            ug_pg_mixed=False,
            sources=[
                Source(institution_id="multi", campus="Islamabad", url="https://yaml-a.edu.pk", format="html"),
                Source(institution_id="multi", campus="Karachi", url="https://yaml-b.edu.pk", format="html"),
            ],
            enabled=True,
        )
        firestore_docs = {
            "multi": {
                "name": "Multi Campus University (firestore, bad)",
                "sources": [
                    {"url": "https://fs-a.edu.pk", "format": "html", "campus": "Lahore"},
                    {"url": "https://fs-b.edu.pk", "format": "html", "campus": "Karachi"},
                    {"url": "https://fs-c.edu.pk", "format": "html", "campus": "Karachi"},
                ],
            }
        }
        result = merge_institutions([yaml_good], firestore_docs)
        assert len(result) == 1
        # Must be the untouched YAML institution -- not a partially-applied
        # Firestore doc keeping just its first (non-colliding) source, and
        # not the colliding Firestore version either.
        assert result[0].name == "Multi Campus University (yaml, good)"
        assert [s.url for s in result[0].sources] == ["https://yaml-a.edu.pk", "https://yaml-b.edu.pk"]
        assert all("fs-" not in s.url for s in result[0].sources)

    def test_three_valid_sources_colliding_second_third_for_new_institution_excludes_entirely(self):
        # No YAML counterpart at all -- must be excluded wholesale, not
        # partially represented by its first (non-colliding) source.
        firestore_docs = {
            "brand_new": {
                "name": "Brand New University",
                "sources": [
                    {"url": "https://fs-a.edu.pk", "format": "html", "campus": "Lahore"},
                    {"url": "https://fs-b.edu.pk", "format": "html", "campus": "Karachi"},
                    {"url": "https://fs-c.edu.pk", "format": "html", "campus": "Karachi"},
                ],
            }
        }
        result = merge_institutions([], firestore_docs)
        assert result == []

    def test_empty_string_campus_collision_in_firestore_doc_rejected(self):
        yaml_good = Institution(
            id="giki2",
            name="GIKI2 (yaml, good)",
            admitting_body=False,
            ug_pg_mixed=False,
            sources=[Source(institution_id="giki2", campus=None, url="https://yaml.edu.pk", format="html")],
            enabled=True,
        )
        firestore_docs = {
            "giki2": {
                "name": "GIKI2 (firestore, bad)",
                "sources": [
                    {"url": "https://fs-a.edu.pk", "format": "html", "campus": None},
                    {"url": "https://fs-b.edu.pk", "format": "html", "campus": ""},
                ],
            }
        }
        result = merge_institutions([yaml_good], firestore_docs)
        assert len(result) == 1
        assert result[0].name == "GIKI2 (yaml, good)"
