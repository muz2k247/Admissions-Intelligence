"""Tests for pipeline/institutions_registry.py -- the Phase R merge of a
curator-managed Firestore `institutions` collection over the git-tracked
config/institutions.yaml seed.

No live network / no live Firestore: mocked the same way test_overrides.py
and test_review.py do it.
"""
from __future__ import annotations

import json

import pytest
import requests

from pipeline.institutions_registry import (
    fetch_institution_docs,
    load_merged_institutions,
    merge_institutions,
)
from scraper.config import Institution, Source


class FakeResponse:
    def __init__(self, payload=None, status_code=200, raise_exc=None, json_exc=None):
        self._payload = payload
        self.status_code = status_code
        self._raise_exc = raise_exc
        self._json_exc = json_exc

    def raise_for_status(self):
        if self._raise_exc:
            raise self._raise_exc
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")

    def json(self):
        if self._json_exc:
            raise self._json_exc
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self._responses = responses if isinstance(responses, list) else [responses]
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append({"url": url, "params": params})
        result = self._responses[min(len(self.calls) - 1, len(self._responses) - 1)]
        if isinstance(result, Exception):
            raise result
        return result


def _fs_string(v):
    return {"stringValue": v}


def _fs_bool(v):
    return {"booleanValue": v}


def _fs_null():
    return {"nullValue": None}


def _fs_array(values):
    return {"arrayValue": {"values": values}}


def _fs_map(fields):
    return {"mapValue": {"fields": fields}}


def _source_map(url="https://new.edu.pk", fmt="html", render=None, campus=None):
    fields = {"url": _fs_string(url), "format": _fs_string(fmt)}
    if render is not None:
        fields["render"] = _fs_string(render)
    if campus is not None:
        fields["campus"] = _fs_string(campus)
    return _fs_map(fields)


def _institution_doc(inst_id, name=None, sources=None, deleted=None, admitting_body=None, ug_pg_mixed=None, enabled=None, extra_fields=None):
    fields = {}
    if name is not None:
        fields["name"] = _fs_string(name)
    if sources is not None:
        fields["sources"] = _fs_array(sources)
    if deleted is not None:
        fields["deleted"] = _fs_bool(deleted)
    if admitting_body is not None:
        fields["admitting_body"] = _fs_bool(admitting_body)
    if ug_pg_mixed is not None:
        fields["ug_pg_mixed"] = _fs_bool(ug_pg_mixed)
    if enabled is not None:
        fields["enabled"] = _fs_bool(enabled)
    if extra_fields:
        fields.update(extra_fields)
    return {
        "name": f"projects/test-proj/databases/(default)/documents/institutions/{inst_id}",
        "fields": fields,
    }


def _yaml_institution(inst_id="giki", name="GIKI", url="https://giki.edu.pk", enabled=True):
    return Institution(
        id=inst_id,
        name=name,
        admitting_body=False,
        ug_pg_mixed=False,
        sources=[Source(institution_id=inst_id, campus=None, url=url, format="html", render="static")],
        enabled=enabled,
    )


# ---------------------------------------------------------------------------
# fetch_institution_docs
# ---------------------------------------------------------------------------

class TestFetchInstitutionDocs:
    def test_fetches_and_decodes(self):
        payload = {"documents": [_institution_doc("giki", name="GIKI", sources=[_source_map()])]}
        session = FakeSession(FakeResponse(payload))

        result = fetch_institution_docs(project_id="test-proj", session=session)

        assert set(result) == {"giki"}
        assert result["giki"]["name"] == "GIKI"

    def test_empty_collection_returns_empty(self):
        session = FakeSession(FakeResponse({"documents": []}))
        assert fetch_institution_docs(project_id="test-proj", session=session) == {}

    def test_follows_pagination(self):
        page1 = {"documents": [_institution_doc("a", name="A", sources=[_source_map()])], "nextPageToken": "tok"}
        page2 = {"documents": [_institution_doc("b", name="B", sources=[_source_map()])]}
        session = FakeSession([FakeResponse(page1), FakeResponse(page2)])

        result = fetch_institution_docs(project_id="test-proj", session=session)

        assert set(result) == {"a", "b"}
        assert len(session.calls) == 2

    def test_network_error_returns_empty_not_raises(self):
        session = FakeSession(requests.ConnectionError("boom"))
        assert fetch_institution_docs(project_id="test-proj", session=session) == {}

    def test_http_error_returns_empty(self):
        session = FakeSession(FakeResponse(status_code=500))
        assert fetch_institution_docs(project_id="test-proj", session=session) == {}

    def test_malformed_json_returns_empty(self):
        session = FakeSession(FakeResponse(json_exc=json.JSONDecodeError("bad", "", 0)))
        assert fetch_institution_docs(project_id="test-proj", session=session) == {}

    def test_no_project_id_returns_empty_without_network(self, monkeypatch):
        monkeypatch.setattr("pipeline.institutions_registry.load_project_id", lambda: None)
        session = FakeSession(FakeResponse({"documents": []}))

        result = fetch_institution_docs(project_id=None, session=session)

        assert result == {}
        assert session.calls == []

    def test_tombstone_doc_decodes_with_only_deleted_field(self):
        payload = {"documents": [_institution_doc("giki", deleted=True)]}
        session = FakeSession(FakeResponse(payload))

        result = fetch_institution_docs(project_id="test-proj", session=session)

        assert result == {"giki": {"deleted": True}}


# ---------------------------------------------------------------------------
# merge_institutions
# ---------------------------------------------------------------------------

class TestMergeInstitutions:
    def test_yaml_only_passes_through_unchanged(self):
        yaml_insts = [_yaml_institution("giki"), _yaml_institution("uet", name="UET", url="https://uet.edu.pk")]
        result = merge_institutions(yaml_insts, {})
        assert result == yaml_insts

    def test_firestore_doc_replaces_yaml_entry_wholesale(self):
        yaml_insts = [_yaml_institution("giki", name="GIKI (old)", url="https://old.giki.edu.pk")]
        firestore_docs = {
            "giki": {
                "name": "GIKI (corrected)",
                "sources": [{"url": "https://giki.edu.pk/admissions/", "format": "html", "render": "static", "campus": None}],
            }
        }
        result = merge_institutions(yaml_insts, firestore_docs)
        assert len(result) == 1
        assert result[0].name == "GIKI (corrected)"
        assert result[0].sources[0].url == "https://giki.edu.pk/admissions/"

    def test_firestore_only_id_is_added(self):
        yaml_insts = [_yaml_institution("giki")]
        firestore_docs = {"new_uni": {"name": "New Uni", "sources": [{"url": "https://new.edu.pk", "format": "html"}]}}
        result = merge_institutions(yaml_insts, firestore_docs)
        assert {i.id for i in result} == {"giki", "new_uni"}
        new_uni = next(i for i in result if i.id == "new_uni")
        assert new_uni.name == "New Uni"
        assert new_uni.enabled is True  # default
        assert new_uni.sources[0].render == "static"  # default

    def test_tombstone_excludes_yaml_origin_institution(self):
        yaml_insts = [_yaml_institution("giki"), _yaml_institution("uet")]
        firestore_docs = {"giki": {"deleted": True}}
        result = merge_institutions(yaml_insts, firestore_docs)
        assert {i.id for i in result} == {"uet"}

    def test_tombstone_of_firestore_only_id_is_simply_absent(self):
        yaml_insts = [_yaml_institution("giki")]
        firestore_docs = {"ghost": {"deleted": True}}
        result = merge_institutions(yaml_insts, firestore_docs)
        assert {i.id for i in result} == {"giki"}

    def test_malformed_doc_missing_name_falls_back_to_yaml_version(self):
        yaml_insts = [_yaml_institution("giki", name="GIKI (yaml)")]
        firestore_docs = {"giki": {"sources": [{"url": "https://x.edu.pk", "format": "html"}]}}  # no name
        result = merge_institutions(yaml_insts, firestore_docs)
        assert len(result) == 1
        assert result[0].name == "GIKI (yaml)"  # YAML version preserved, bad doc ignored

    def test_malformed_doc_for_new_id_is_excluded_not_crashed(self):
        yaml_insts = [_yaml_institution("giki")]
        firestore_docs = {"new_uni": {"name": "New Uni"}}  # missing sources
        result = merge_institutions(yaml_insts, firestore_docs)
        assert {i.id for i in result} == {"giki"}

    def test_missing_sources_is_rejected(self):
        yaml_insts = []
        firestore_docs = {"x": {"name": "X", "sources": []}}
        result = merge_institutions(yaml_insts, firestore_docs)
        assert result == []

    def test_source_missing_url_rejects_whole_institution(self):
        yaml_insts = [_yaml_institution("giki", name="GIKI (yaml)")]
        firestore_docs = {"giki": {"name": "GIKI (bad)", "sources": [{"format": "html"}]}}
        result = merge_institutions(yaml_insts, firestore_docs)
        assert result[0].name == "GIKI (yaml)"

    def test_invalid_render_rejects_whole_institution(self):
        yaml_insts = [_yaml_institution("giki", name="GIKI (yaml)")]
        firestore_docs = {
            "giki": {"name": "GIKI (bad)", "sources": [{"url": "https://x.edu.pk", "format": "html", "render": "pdf-only"}]}
        }
        result = merge_institutions(yaml_insts, firestore_docs)
        assert result[0].name == "GIKI (yaml)"

    def test_non_bool_deleted_field_is_not_treated_as_tombstone(self):
        yaml_insts = [_yaml_institution("giki", name="GIKI (yaml)")]
        # `deleted` decoded as a string (malformed write) -- must not match
        # `is True` and accidentally exclude the institution.
        firestore_docs = {"giki": {"deleted": "true", "name": "GIKI (str-deleted)", "sources": [{"url": "https://x.edu.pk", "format": "html"}]}}
        result = merge_institutions(yaml_insts, firestore_docs)
        assert len(result) == 1
        assert result[0].name == "GIKI (str-deleted)"

    def test_non_bool_admitting_body_defaults_false(self):
        firestore_docs = {"x": {"name": "X", "admitting_body": "yes", "sources": [{"url": "https://x.edu.pk", "format": "html"}]}}
        result = merge_institutions([], firestore_docs)
        assert result[0].admitting_body is False

    def test_campus_defaults_to_none_when_absent_or_wrong_type(self):
        firestore_docs = {
            "x": {"name": "X", "sources": [{"url": "https://x.edu.pk", "format": "html", "campus": 123}]}
        }
        result = merge_institutions([], firestore_docs)
        assert result[0].sources[0].campus is None

    def test_output_order_yaml_first_then_new_ids_sorted(self):
        yaml_insts = [_yaml_institution("uet"), _yaml_institution("giki")]
        firestore_docs = {
            "zzz_new": {"name": "ZZZ", "sources": [{"url": "https://zzz.edu.pk", "format": "html"}]},
            "aaa_new": {"name": "AAA", "sources": [{"url": "https://aaa.edu.pk", "format": "html"}]},
        }
        result = merge_institutions(yaml_insts, firestore_docs)
        assert [i.id for i in result] == ["uet", "giki", "aaa_new", "zzz_new"]

    def test_non_dict_firestore_doc_value_is_ignored(self):
        yaml_insts = [_yaml_institution("giki", name="GIKI (yaml)")]
        result = merge_institutions(yaml_insts, {"giki": "not-a-dict"})
        assert result[0].name == "GIKI (yaml)"


# ---------------------------------------------------------------------------
# load_merged_institutions
# ---------------------------------------------------------------------------

class TestLoadMergedInstitutions:
    def test_glues_yaml_load_and_firestore_fetch(self, monkeypatch, tmp_path):
        config_path = tmp_path / "institutions.yaml"
        config_path.write_text(
            """
institutions:
  - id: giki
    name: GIKI
    sources:
      - campus: null
        url: "https://giki.edu.pk"
        format: html
""",
            encoding="utf-8",
        )
        session = FakeSession(FakeResponse({
            "documents": [_institution_doc("new_uni", name="New Uni", sources=[_source_map(url="https://new.edu.pk")])],
        }))

        result = load_merged_institutions(config_path=config_path, project_id="test-proj", session=session)

        assert {i.id for i in result} == {"giki", "new_uni"}

    def test_firestore_failure_falls_back_to_yaml_only(self, tmp_path):
        config_path = tmp_path / "institutions.yaml"
        config_path.write_text(
            """
institutions:
  - id: giki
    name: GIKI
    sources:
      - campus: null
        url: "https://giki.edu.pk"
        format: html
""",
            encoding="utf-8",
        )
        session = FakeSession(requests.ConnectionError("boom"))

        result = load_merged_institutions(config_path=config_path, project_id="test-proj", session=session)

        assert [i.id for i in result] == ["giki"]


# ---------------------------------------------------------------------------
# Additional QA coverage: mixed-validity source lists, multi-campus survival,
# extra unrecognized fields, purity of merge_institutions, a real multi-source
# institution shape (Air University) round-tripped unchanged, and the
# MAX_PAGES cap in fetch_institution_docs.
# ---------------------------------------------------------------------------

class TestMixedValiditySourceLists:
    def test_one_bad_source_among_valid_ones_rejects_whole_institution(self):
        """A source list with one good entry and one bad entry (missing url)
        must reject the WHOLE institution and fall back to YAML -- not
        partially apply just the good source."""
        yaml_insts = [_yaml_institution("air_university", name="Air University (yaml)")]
        firestore_docs = {
            "air_university": {
                "name": "Air University (bad)",
                "sources": [
                    {"url": "https://portals.au.edu.pk/admissions/", "format": "html", "campus": "Islamabad"},
                    {"format": "html", "campus": "Karachi"},  # missing url
                ],
            }
        }
        result = merge_institutions(yaml_insts, firestore_docs)
        assert len(result) == 1
        assert result[0].name == "Air University (yaml)"  # fell back to yaml, bad doc fully ignored

    def test_one_bad_source_for_new_id_excludes_the_new_institution_entirely(self):
        firestore_docs = {
            "new_multi": {
                "name": "New Multi",
                "sources": [
                    {"url": "https://a.edu.pk", "format": "html"},
                    {"url": "https://b.edu.pk", "format": "html", "render": "not-a-valid-mode"},
                ],
            }
        }
        result = merge_institutions([], firestore_docs)
        assert result == []  # whole institution excluded, not just the bad source dropped


class TestMultiCampusSurvival:
    def test_multiple_sources_survive_merge_intact(self):
        """A curator-edited multi-campus institution (Air University shape:
        two sources, one per campus) must come through merge_institutions
        with both sources intact, in order, each retaining its own campus."""
        firestore_docs = {
            "air_university": {
                "name": "Air University",
                "sources": [
                    {"url": "https://portals.au.edu.pk/admissions/", "format": "html+pdf", "campus": "Islamabad & Punjab campuses"},
                    {"url": "https://kc.au.edu.pk/Pages/Admission/admission_schedule.aspx", "format": "html+pdf", "campus": "Karachi"},
                ],
            }
        }
        result = merge_institutions([], firestore_docs)
        assert len(result) == 1
        au = result[0]
        assert len(au.sources) == 2
        assert au.sources[0].campus == "Islamabad & Punjab campuses"
        assert au.sources[0].url == "https://portals.au.edu.pk/admissions/"
        assert au.sources[1].campus == "Karachi"
        assert au.sources[1].url == "https://kc.au.edu.pk/Pages/Admission/admission_schedule.aspx"
        assert all(s.institution_id == "air_university" for s in au.sources)

    def test_multi_source_yaml_institution_replaced_wholesale_by_multi_source_firestore_doc(self):
        yaml_insts = [
            Institution(
                id="air_university",
                name="Air University (old)",
                admitting_body=False,
                ug_pg_mixed=True,
                sources=[
                    Source(institution_id="air_university", campus="Islamabad & Punjab campuses", url="https://old1.edu.pk", format="html+pdf", render="static"),
                    Source(institution_id="air_university", campus="Karachi", url="https://old2.edu.pk", format="html+pdf", render="static"),
                ],
                enabled=True,
            )
        ]
        firestore_docs = {
            "air_university": {
                "name": "Air University (corrected)",
                "sources": [
                    {"url": "https://new1.edu.pk", "format": "html", "campus": "Islamabad & Punjab campuses"},
                    {"url": "https://new2.edu.pk", "format": "html", "campus": "Karachi"},
                ],
            }
        }
        result = merge_institutions(yaml_insts, firestore_docs)
        assert len(result) == 1
        assert result[0].name == "Air University (corrected)"
        assert [s.url for s in result[0].sources] == ["https://new1.edu.pk", "https://new2.edu.pk"]


class TestExtraUnrecognizedFields:
    def test_extra_unrecognized_top_level_field_is_ignored(self):
        firestore_docs = {
            "x": {
                "name": "X",
                "sources": [{"url": "https://x.edu.pk", "format": "html"}],
                "unknown_field": "value curators cannot legitimately inject via this doc shape",
                "another_extra": {"nested": True},
            }
        }
        result = merge_institutions([], firestore_docs)
        assert len(result) == 1
        assert result[0].name == "X"
        # No AttributeError, no crash, and the built Institution has no
        # smuggled extra attribute (dataclass has fixed fields only).
        assert not hasattr(result[0], "unknown_field")

    def test_extra_unrecognized_field_inside_a_source_is_ignored(self):
        firestore_docs = {
            "x": {
                "name": "X",
                "sources": [{"url": "https://x.edu.pk", "format": "html", "campus": None, "priority": "high", "notes": "curator note"}],
            }
        }
        result = merge_institutions([], firestore_docs)
        assert len(result) == 1
        src = result[0].sources[0]
        assert src.url == "https://x.edu.pk"
        assert not hasattr(src, "priority")


class TestMergePurity:
    def test_yaml_institutions_list_is_not_mutated(self):
        yaml_insts = [_yaml_institution("giki", name="GIKI (yaml)"), _yaml_institution("uet", name="UET (yaml)")]
        original_ids = [i.id for i in yaml_insts]
        original_snapshot = list(yaml_insts)  # shallow copy of the list itself

        firestore_docs = {
            "giki": {"name": "GIKI (corrected)", "sources": [{"url": "https://new.edu.pk", "format": "html"}]},
            "deleted_ghost": {"deleted": True},
            "brand_new": {"name": "Brand New", "sources": [{"url": "https://brandnew.edu.pk", "format": "html"}]},
        }

        merge_institutions(yaml_insts, firestore_docs)

        # The input list must be untouched: same length, same order, same
        # objects (dataclasses are frozen so object identity confirms no
        # in-place field mutation was even attempted).
        assert [i.id for i in yaml_insts] == original_ids
        assert yaml_insts == original_snapshot
        assert yaml_insts[0].name == "GIKI (yaml)"  # not overwritten in place
        assert len(yaml_insts) == 2

    def test_firestore_docs_dict_is_not_mutated(self):
        firestore_docs = {
            "giki": {"name": "GIKI", "sources": [{"url": "https://giki.edu.pk", "format": "html"}]},
        }
        import copy
        snapshot = copy.deepcopy(firestore_docs)

        merge_institutions([], firestore_docs)

        assert firestore_docs == snapshot

    def test_calling_merge_twice_with_same_inputs_yields_equal_results(self):
        yaml_insts = [_yaml_institution("giki")]
        firestore_docs = {"new_uni": {"name": "New Uni", "sources": [{"url": "https://new.edu.pk", "format": "html"}]}}
        result1 = merge_institutions(yaml_insts, firestore_docs)
        result2 = merge_institutions(yaml_insts, firestore_docs)
        assert result1 == result2


class TestRealInstitutionShapeRoundTrip:
    def test_air_university_multi_source_yaml_shape_passes_through_unchanged(self):
        """Round-trip a real multi-campus institution shape (adapted from
        config/institutions.yaml's air_university entry) through the merge
        with no matching Firestore doc -- must pass through unchanged, same
        as any other untouched YAML id."""
        air_university = Institution(
            id="air_university",
            name="Air University",
            admitting_body=False,
            ug_pg_mixed=True,
            sources=[
                Source(
                    institution_id="air_university",
                    campus="Islamabad & Punjab campuses",
                    url="https://portals.au.edu.pk/admissions/",
                    format="html+pdf",
                    render="static",
                ),
                Source(
                    institution_id="air_university",
                    campus="Karachi",
                    url="https://kc.au.edu.pk/Pages/Admission/admission_schedule.aspx",
                    format="html+pdf",
                    render="static",
                ),
            ],
            enabled=True,
        )
        yaml_insts = [air_university]
        result = merge_institutions(yaml_insts, {})
        assert result == [air_university]
        assert result[0] is air_university  # untouched id passes through the same object


class TestFetchInstitutionDocsMaxPagesCap:
    def test_hitting_max_pages_cap_returns_partial_results_not_hang_or_raise(self, monkeypatch):
        """Mirrors test_review.py's fetch_review_decisions MAX_PAGES coverage:
        a server that always echoes a nextPageToken must not hang or crash
        fetch_institution_docs -- it degrades to whatever was gathered
        within the MAX_PAGES bound."""
        from pipeline import _firestore

        monkeypatch.setattr(_firestore, "MAX_PAGES", 3)

        responses = [
            FakeResponse({"documents": [_institution_doc(f"inst{i}", name=f"Inst {i}", sources=[_source_map()])], "nextPageToken": "tok"})
            for i in range(3)
        ]
        session = FakeSession(responses)

        result = fetch_institution_docs(project_id="test-proj", session=session)

        assert len(session.calls) == 3
        assert set(result) == {"inst0", "inst1", "inst2"}
