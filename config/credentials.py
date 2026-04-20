"""
Credential loader — reads from environment or credentials.env file.
Never hardcodes secrets.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


_CREDENTIALS_FILE = Path("<workspace>/credentials.env")
_loaded: bool = False


def _load_env_file() -> None:
    """Load credentials.env into os.environ if not already loaded."""
    global _loaded
    if _loaded:
        return
    if _CREDENTIALS_FILE.exists():
        for line in _CREDENTIALS_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    _loaded = True


def get_credential(name: str) -> Optional[str]:
    """Get a credential by name from env or credentials.env."""
    _load_env_file()
    return os.environ.get(name)


def require_credential(name: str) -> str:
    """Get a credential or raise if missing."""
    value = get_credential(name)
    if not value:
        raise EnvironmentError(f"Required credential '{name}' not found in environment or credentials.env")
    return value
