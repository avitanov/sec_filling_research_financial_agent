"""Check that downloaded SEC files match the frozen manifest."""

import csv
import hashlib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_downloaded_files_match_manifest_checksums() -> None:
    """Verify every downloaded file has the recorded SHA-256 checksum."""
    manifest_path = PROJECT_ROOT / "data" / "manifest.csv"
    with manifest_path.open(newline="", encoding="utf-8") as manifest_file:
        rows = csv.DictReader(manifest_file)
        for row in rows:
            file_path = PROJECT_ROOT / row["local_path"]
            actual_hash = hashlib.sha256(file_path.read_bytes()).hexdigest()
            assert actual_hash == row["sha256"]
