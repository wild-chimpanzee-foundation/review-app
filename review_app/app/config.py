import sys
from pathlib import Path

import yaml


def get_app_dir() -> Path:
    """Run directory: parent of _MEIPASS when frozen, repo root in dev."""
    if getattr(sys, "frozen", False):
        # When frozen, _MEIPASS points to the internal temp dir
        # We want the directory where the executable itself is located.
        return Path(sys.executable).parent
    # In dev, use the project root
    return Path(__file__).parents[2]


def get_config_path() -> Path:
    return get_app_dir() / "config.yaml"


def get_default_db_path() -> Path:
    return get_app_dir() / "review_data.db"


def get_bundled_default_config_path() -> Path:
    bundle_dir = Path(__file__).parent.parent / "data"
    return bundle_dir / "default_config.yaml"


def get_bundled_species_csv() -> str | None:
    bundle_dir = Path(__file__).parent.parent / "data"
    bundled = bundle_dir / "species.csv"
    if bundled.exists():
        return str(bundled)
    return None


def get_bundled_behaviors_csv() -> str | None:
    bundle_dir = Path(__file__).parent.parent / "data"
    # Unified naming: species_behaviors.csv
    bundled = bundle_dir / "species_behaviors.csv"
    if bundled.exists():
        return str(bundled)
    return None


def load_config() -> dict:
    path = get_config_path()
    if not path.exists():
        return {}
    try:
        with open(path) as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        print(f"Error loading config at {path}: {e}")
        return {}


def save_config(config: dict) -> None:
    path = get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


def update_config_key(key: str, value) -> None:
    config = load_config()
    config[key] = value
    save_config(config)
