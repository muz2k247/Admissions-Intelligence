"""Manual test harness for the pipeline orchestration.

This script simulates the full pipeline with mock data to verify all stages
work correctly before scheduling the cloud job.

Usage:
    python -m pipeline.test_orchestration

Output:
    Creates test data in .tmp/manual_test/ directory
    Reports pass/fail for each stage
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Stage test: import modules first
try:
    from pipeline.run_full import stage_2_chunk, stage_4_build, stage_5_publish
    from extraction.classify import load_classifier_results
    print("[PASS] All pipeline modules import successfully")
except ImportError as e:
    print(f"[FAIL] Failed to import pipeline modules: {e}")
    sys.exit(1)


def create_test_data() -> Path:
    """Create mock scraped data for testing."""
    print("\n" + "="*70)
    print("STAGE 1: CREATE TEST DATA (Mock Scraper Output)")
    print("="*70)

    test_dir = Path(".tmp/manual_test")
    scraped_dir = test_dir / "scraped"
    scraped_dir.mkdir(parents=True, exist_ok=True)

    # Create several mock institutions
    institutions = [
        {
            "institution_id": "giki",
            "campus": None,
            "source_url": "https://admissions.giki.edu.pk",
            "fetched_at": "2026-01-15T10:00:00Z",
            "html": """
                <html><body>
                <h1>GIKI Admissions</h1>
                <p>Bachelor of Science (BS) in Computer Science</p>
                <p>Application Deadline: June 30, 2026</p>
                <p>Application Fee: PKR 5,000</p>
                <p>Offered Programs: BS Computer Science, BS Engineering</p>
                </body></html>
            """,
            "pdfs": [],
            "error": None,
        },
        {
            "institution_id": "uhs",
            "campus": None,
            "source_url": "https://public-mbbs.uhs.edu.pk",
            "fetched_at": "2026-01-15T10:05:00Z",
            "html": """
                <html><body>
                <h1>UHS MBBS & BDS Admissions</h1>
                <p>Undergraduate: MBBS (5 years) and BDS (4 years)</p>
                <p>Entry Test: MDCAT</p>
                <p>Deadline: April 15, 2026</p>
                <p>Fee Structure: Available on portal</p>
                <p>Postgraduate: MS & PhD Programs also offered</p>
                </body></html>
            """,
            "pdfs": [],
            "error": None,
        },
        {
            "institution_id": "lums",
            "campus": None,
            "source_url": "https://admissions.lums.edu.pk",
            "fetched_at": "2026-01-15T10:10:00Z",
            "html": """
                <html><body>
                <h1>LUMS Admissions Portal</h1>
                <p>Bachelor of Science Honours (4 years)</p>
                <p>Master's Programs available in multiple disciplines</p>
                <p>Application Window: Open now</p>
                <p>Late Fee: PKR 2,000 additional</p>
                </body></html>
            """,
            "pdfs": [],
            "error": None,
        },
    ]

    created = 0
    for inst in institutions:
        out_path = scraped_dir / f"{inst['institution_id']}.json"
        out_path.write_text(json.dumps(inst, indent=2))
        created += 1

    print(f"[PASS] Created {created} mock institutions in {scraped_dir}")
    return test_dir


def test_stage_2(test_dir: Path) -> bool:
    """Test Stage 2: Chunking."""
    print("\n" + "="*70)
    print("STAGE 2: CHUNKING")
    print("="*70)

    scraped_dir = test_dir / "scraped"
    chunks_out = test_dir / "chunks.json"

    try:
        exit_code = stage_2_chunk(scraped_dir, chunks_out)
        if exit_code != 0:
            print(f"[FAIL] Stage 2 returned exit code {exit_code}")
            return False

        if not chunks_out.exists():
            print("[FAIL] Chunks output file was not created")
            return False

        chunks = json.loads(chunks_out.read_text())
        print(f"[PASS] {len(chunks)} chunks produced successfully")
        return True

    except Exception as e:
        print(f"[FAIL] Stage 2 failed with exception: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_stage_3(test_dir: Path) -> bool:
    """Test Stage 3: Create mock classifier output."""
    print("\n" + "="*70)
    print("STAGE 3: CLASSIFICATION (Mock)")
    print("="*70)

    chunks_file = test_dir / "chunks.json"
    classified_file = test_dir / "classified.json"

    try:
        chunks = json.loads(chunks_file.read_text())
        print(f"[PASS] Read {len(chunks)} chunks from {chunks_file.name}")

        # Create mock classifier output
        # Assign roughly 60% UG, 30% PG, 10% Ambiguous
        ug_count = int(len(chunks) * 0.6)
        pg_count = int(len(chunks) * 0.3)

        classified = {
            "Undergraduate": [chunks[i]["id"] for i in range(ug_count)],
            "Postgraduate": [chunks[i]["id"] for i in range(ug_count, ug_count + pg_count)],
            "Ambiguous": [
                {"id": chunks[i]["id"], "reason": "mixed-degree-level"}
                for i in range(ug_count + pg_count, len(chunks))
            ],
        }

        classified_file.write_text(json.dumps(classified, indent=2))
        print(f"[PASS] Created mock classifier output:")
        print(f"       Undergraduate: {len(classified['Undergraduate'])}")
        print(f"       Postgraduate: {len(classified['Postgraduate'])}")
        print(f"       Ambiguous: {len(classified['Ambiguous'])}")
        return True

    except Exception as e:
        print(f"[FAIL] Stage 3 mock failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_stage_4(test_dir: Path) -> bool:
    """Test Stage 4: Extraction Build."""
    print("\n" + "="*70)
    print("STAGE 4: EXTRACTION BUILD")
    print("="*70)

    scraped_dir = test_dir / "scraped"
    classified_file = test_dir / "classified.json"
    extracted_dir = test_dir / "extracted"

    try:
        exit_code = stage_4_build(scraped_dir, classified_file, extracted_dir)
        if exit_code != 0:
            print(f"[FAIL] Stage 4 returned exit code {exit_code}")
            return False

        records = list(extracted_dir.glob("*.json"))
        print(f"[PASS] {len(records)} extracted records created")

        # Verify records are valid
        for record_file in records:
            try:
                record = json.loads(record_file.read_text())
                # Check required fields
                required = ["source_url", "chunk_id", "degree_level", "institution_id"]
                missing = [f for f in required if f not in record]
                if missing:
                    print(f"[FAIL] Record {record_file.name} missing fields: {missing}")
                    return False
            except json.JSONDecodeError:
                print(f"[FAIL] Record {record_file.name} is not valid JSON")
                return False

        print("[PASS] All extracted records have required fields")
        return True

    except Exception as e:
        print(f"[FAIL] Stage 4 failed with exception: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_stage_5_publish(test_dir: Path) -> bool:
    """Test Stage 5: build the static data/*.json artifacts the dashboard fetches directly."""
    print("\n" + "="*70)
    print("STAGE 5: BUILD & PUBLISH STATIC DATA")
    print("="*70)

    extracted_dir = test_dir / "extracted"
    publish_dir = test_dir / "publish"

    try:
        exit_code = stage_5_publish(extracted_dir, publish_dir)
        if exit_code != 0:
            print(f"[FAIL] Stage 5 returned exit code {exit_code}")
            return False

        records_file = publish_dir / "records.json"
        institutions_file = publish_dir / "institutions.json"
        if not records_file.exists() or not institutions_file.exists():
            print("[FAIL] Stage 5 did not produce both records.json and institutions.json")
            return False

        records = json.loads(records_file.read_text())
        institutions = json.loads(institutions_file.read_text())
        print(f"[PASS] Published {len(records)} record(s) and {len(institutions)} institution(s)")

        if records:
            print(f"[PASS] Sample record has {len(records[0])} fields")

        return True

    except Exception as e:
        print(f"[FAIL] Stage 5 publish failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def main() -> None:
    """Run full manual test suite."""
    print("\n" + "="*70)
    print("PIPELINE MANUAL TEST SUITE")
    print("="*70)
    print("\nThis script tests the full pipeline with mock data.")
    print("No live network calls are made.\n")

    # Stage 1: Create test data
    test_dir = create_test_data()

    # Stage 2: Chunk
    if not test_stage_2(test_dir):
        print("\n[FAIL] Stage 2 failed. Aborting.")
        sys.exit(1)

    # Stage 3: Mock classifier
    if not test_stage_3(test_dir):
        print("\n[FAIL] Stage 3 mock failed. Aborting.")
        sys.exit(1)

    # Stage 4: Extract
    if not test_stage_4(test_dir):
        print("\n[FAIL] Stage 4 failed. Aborting.")
        sys.exit(1)

    # Stage 5: Build & publish static data
    if not test_stage_5_publish(test_dir):
        print("\n[FAIL] Stage 5 publish failed. Aborting.")
        sys.exit(1)

    # Final summary
    print("\n" + "="*70)
    print("ALL TESTS PASSED - PIPELINE IS READY FOR SCHEDULING")
    print("="*70)
    print(f"\nTest data created in: {test_dir}")
    print("\nNext steps:")
    print("  1. The pipeline orchestration is ready to be scheduled")
    print("  2. Use /schedule to set up the cloud job")
    print("  3. First scheduled run will fetch live data from institutions")
    print("  4. Dashboard fetches published data directly — no backend to run")
    print("\n" + "="*70)


if __name__ == "__main__":
    main()
