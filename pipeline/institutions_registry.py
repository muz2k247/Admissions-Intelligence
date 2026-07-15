"""Admin-managed institutions (Phase R): merge a curator-managed Firestore
`institutions` collection over the git-tracked `config/institutions.yaml`
seed, so a curator-added or curator-edited institution is picked up by both
the scraper (stage_1_scrape) and the published institutions.json
(stage_5_publish) on the very next pipeline run -- not just the dashboard's
institution dropdown.

Same unauthenticated-REST, graceful-degradation contract as
pipeline/overrides.py and pipeline/review.py (see those modules' docstrings
for the full rationale): no credential is needed because Firestore security
rules -- not key secrecy -- gate writes to an allowlisted curator UID, and
any fetch failure here must degrade to the YAML-only seed rather than block
or corrupt a pipeline run.

Merge semantics, by institution id (Firestore wins on conflicts):
- A YAML id with no matching Firestore doc passes through unchanged.
- A YAML id with a matching, non-deleted Firestore doc has that doc WHOLLY
  REPLACE the YAML entry -- institution-level replace, not a deep field
  merge. A curator edits an institution as one coherent unit (its whole
  source list included), and the admin CMS always writes back the full
  current shape, so partial-field replace is never needed and would only
  invite drift between a doc's fields and reality.
- A Firestore id not present in the YAML seed, not deleted, is a
  curator-ADDED institution.
- Any Firestore doc with `deleted: true` (a tombstone) excludes that
  institution entirely, whether it originated from YAML or Firestore. A
  tombstone doc needs no other fields.
- A malformed/invalid non-deleted Firestore doc (missing name, empty/absent
  sources, a source with a bad url/format/render) is skipped with a WARN,
  falling back to the YAML version for that id if one exists, else excluded
  -- this module never raises on bad data and never crashes a run.
- Any Firestore fetch failure (network/parse/no project id) falls back to
  YAML-only, unchanged from pre-Phase-R behavior -- the YAML seed is always
  known-good.
"""
from __future__ import annotations

import sys

import requests

from pipeline._firestore import (
    FIRESTORE_EXCEPTIONS,
    decode_value,
    fetch_collection,
    load_project_id,
)
from scraper.config import (
    DEFAULT_CONFIG_PATH,
    VALID_RENDER_MODES,
    Institution,
    Source,
    find_campus_collision,
    load_institutions,
)

_DEFAULT_TIMEOUT = 30


def _build_institution(institution_id: str, decoded: dict) -> Institution | None:
    """Turn one decoded (non-deleted) Firestore institution doc into an
    Institution, or None if it's malformed. Never raises; a malformed doc is
    the caller's cue to fall back to the YAML version (if any) instead."""
    name = decoded.get("name")
    if not isinstance(name, str) or not name:
        print(f"WARN  institution {institution_id!r}: missing/invalid name -- ignored", file=sys.stderr)
        return None

    admitting_body = decoded.get("admitting_body")
    if not isinstance(admitting_body, bool):
        admitting_body = False

    ug_pg_mixed = decoded.get("ug_pg_mixed")
    if not isinstance(ug_pg_mixed, bool):
        ug_pg_mixed = False

    enabled = decoded.get("enabled")
    if not isinstance(enabled, bool):
        enabled = True

    raw_sources = decoded.get("sources")
    if not isinstance(raw_sources, list) or not raw_sources:
        print(f"WARN  institution {institution_id!r}: missing/empty sources -- ignored", file=sys.stderr)
        return None

    sources: list[Source] = []
    for raw_src in raw_sources:
        if not isinstance(raw_src, dict):
            print(f"WARN  institution {institution_id!r}: a source entry is not an object -- ignored", file=sys.stderr)
            return None
        url = raw_src.get("url")
        fmt = raw_src.get("format")
        if not isinstance(url, str) or not url or not isinstance(fmt, str) or not fmt:
            print(f"WARN  institution {institution_id!r}: a source is missing url/format -- ignored", file=sys.stderr)
            return None
        render = raw_src.get("render", "static")
        if render not in VALID_RENDER_MODES:
            print(
                f"WARN  institution {institution_id!r}: source render={render!r} must be one of "
                f"{VALID_RENDER_MODES} -- ignored",
                file=sys.stderr,
            )
            return None
        campus = raw_src.get("campus")
        if campus is not None and not isinstance(campus, str):
            campus = None
        sources.append(Source(institution_id=institution_id, campus=campus, url=url, format=fmt, render=render))

    collision = find_campus_collision(sources)
    if collision is not None:
        print(
            f"WARN  institution {institution_id!r}: sources {collision[0]!r} and {collision[1]!r} "
            "would collide on the same scraped-file name / chunk_id -- ignored",
            file=sys.stderr,
        )
        return None

    return Institution(
        id=institution_id,
        name=name,
        admitting_body=admitting_body,
        ug_pg_mixed=ug_pg_mixed,
        sources=sources,
        enabled=enabled,
    )


def merge_institutions(yaml_institutions: list[Institution], firestore_docs: dict[str, dict]) -> list[Institution]:
    """Merge `firestore_docs` (as returned by fetch_institution_docs) over
    `yaml_institutions` (as returned by scraper.config.load_institutions),
    per this module's merge semantics. Pure -- no I/O.

    Output order: YAML order first (for ids still present after tombstones),
    then curator-added ids appended sorted by id -- deterministic across
    runs regardless of Firestore's document iteration order."""
    by_id: dict[str, Institution] = {inst.id: inst for inst in yaml_institutions}
    yaml_ids = [inst.id for inst in yaml_institutions]

    for institution_id, decoded in firestore_docs.items():
        if not isinstance(decoded, dict):
            continue
        if decoded.get("deleted") is True:
            by_id.pop(institution_id, None)
            continue
        built = _build_institution(institution_id, decoded)
        if built is None:
            # Malformed: leave whatever was already in by_id (the YAML
            # version, if any) untouched rather than dropping it.
            continue
        by_id[institution_id] = built

    new_ids_sorted = sorted(i for i in by_id if i not in yaml_ids)
    final_order = [i for i in yaml_ids if i in by_id] + new_ids_sorted
    return [by_id[i] for i in final_order]


def _decode_institution_document(doc: dict) -> tuple[str, dict] | None:
    name = doc.get("name")
    if not isinstance(name, str) or not name:
        return None
    institution_id = name.rsplit("/", 1)[-1]

    raw_fields = doc.get("fields", {})
    if not isinstance(raw_fields, dict):
        return institution_id, {}
    return institution_id, {k: decode_value(v) for k, v in raw_fields.items()}


def fetch_institution_docs(
    project_id: str | None = None,
    session: requests.Session | None = None,
    timeout: int = _DEFAULT_TIMEOUT,
) -> dict[str, dict]:
    """Fetch the `institutions` collection, returning {institution_id:
    decoded_fields}. Returns {} on ANY failure (no project id, network
    error, non-200, malformed JSON) -- a Firestore problem must never block
    a pipeline run; merge_institutions() with an empty dict is a no-op that
    reproduces pre-Phase-R (YAML-only) behavior exactly."""
    project_id = project_id or load_project_id()
    if not project_id:
        print(
            "WARN  no Firebase project id (.firebaserc unreadable) -- using YAML institutions only",
            file=sys.stderr,
        )
        return {}

    session = session or requests.Session()
    try:
        documents = fetch_collection("institutions", project_id, session, timeout)
    except FIRESTORE_EXCEPTIONS as exc:
        print(f"WARN  could not fetch admin-managed institutions ({exc}) -- using YAML institutions only", file=sys.stderr)
        return {}

    docs: dict[str, dict] = {}
    for doc in documents:
        decoded = _decode_institution_document(doc)
        if decoded is not None:
            institution_id, fields = decoded
            docs[institution_id] = fields
    return docs


def load_merged_institutions(
    config_path=DEFAULT_CONFIG_PATH,
    project_id: str | None = None,
    session: requests.Session | None = None,
) -> list[Institution]:
    """The one entry point stage_1_scrape and stage_5_publish both call:
    YAML seed merged with the curator-managed Firestore `institutions`
    collection, tombstones applied.

    Belt-and-suspenders beyond fetch_institution_docs()'s own "never raise"
    contract: this function feeds BOTH the scraper and the publish stage of
    an unattended weekly pipeline run, so an exception here would abort the
    entire run, not just skip the institutions merge -- a wider blast radius
    than any single fetch_* call elsewhere in this pipeline. Any unexpected
    exception (not just the FIRESTORE_EXCEPTIONS fetch_institution_docs
    already catches internally) degrades to the YAML-only seed rather than
    propagating.

    Known accepted gap: stage_1_scrape and stage_5_publish each call this
    function independently (two separate Firestore round-trips per pipeline
    run, a few minutes apart per .github/workflows/pipeline.yml). A curator
    edit landing in that window means the run could scrape one snapshot of
    admin-managed institutions and publish metadata for a slightly different
    one. Not fixed here: curator institution edits are rare, manual,
    admin-only actions, and passing a resolved list across separate GitHub
    Actions steps would need its own persistence mechanism for a narrow,
    low-frequency race -- not worth the added machinery at this scale."""
    yaml_institutions = load_institutions(config_path)
    try:
        firestore_docs = fetch_institution_docs(project_id=project_id, session=session)
    except Exception as exc:  # noqa: BLE001 -- see docstring: any failure here must degrade, never abort the run
        print(f"WARN  unexpected failure resolving admin-managed institutions ({exc}) -- using YAML institutions only", file=sys.stderr)
        firestore_docs = {}
    return merge_institutions(yaml_institutions, firestore_docs)
