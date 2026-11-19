import hashlib
from pathlib import Path


def test_published_report_checksums_match_downloadable_bytes():
    reports = Path(__file__).resolve().parents[2] / "benchmarks" / "reports"
    checksums = sorted(reports.glob("*.sha256"))
    assert checksums
    for checksum in checksums:
        for line in checksum.read_text(encoding="utf-8").splitlines():
            expected, filename = line.split(maxsplit=1)
            assert hashlib.sha256((reports / filename).read_bytes()).hexdigest() == expected
