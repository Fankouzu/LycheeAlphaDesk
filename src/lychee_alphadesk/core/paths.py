from pathlib import Path


def discover_project_root(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()
    for candidate in [current, *current.parents]:
        if (candidate / "examples" / "demo" / "policy.yaml").exists():
            return candidate
    return Path(__file__).resolve().parents[3]


PROJECT_ROOT = discover_project_root()
DEMO_ROOT = PROJECT_ROOT / "examples" / "demo"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / ".alphadesk"
