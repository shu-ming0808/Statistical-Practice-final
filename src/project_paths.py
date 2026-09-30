"""All paths are relative to this project, never to the shell working directory."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "original_data"
INTERIM = ROOT / "data" / "interim"
PROCESSED = ROOT / "data" / "processed"
REPORTS = ROOT / "results" / "preprocessing"


def prepare_directories():
    for path in (INTERIM / "clean_od", PROCESSED, REPORTS, ROOT / ".local" / "duckdb_tmp"):
        path.mkdir(parents=True, exist_ok=True)
