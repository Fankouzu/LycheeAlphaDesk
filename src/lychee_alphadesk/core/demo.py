from pathlib import Path

REQUIRED_DEMO_FILES = [
    "policy.yaml",
    "portfolio.csv",
    "market_data.csv",
    "news.jsonl",
    "filings.jsonl",
]


def check_demo_workspace(demo_root: Path) -> list[Path]:
    missing = [demo_root / name for name in REQUIRED_DEMO_FILES if not (demo_root / name).exists()]
    if missing:
        return missing
    return []
