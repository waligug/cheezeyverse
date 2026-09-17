"""Configuration for the commissioner app: environment variables, plus an optional `.env`.

Nothing here raises on import. A missing Supabase key is a normal state - the codec, the
driver and the universe generator all work offline, and only `commissioner.store` needs
credentials. `store.StoreNotConfigured` is what gets raised, and only when something
actually tries to talk to Supabase.

Precedence, highest first:
    1. a real environment variable
    2. a line in the project-root `.env`
    3. the default baked in here

`.env` format: `KEY=value` per line, `#` comments, optional `export ` prefix, optional
surrounding quotes. No interpolation, no multi-line values - see `.env.example`.
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"

_DEFAULTS = {
    "SUPABASE_URL": "",
    "SUPABASE_SERVICE_KEY": "",
    "SUPABASE_TIMEOUT": "20",
    # where `python -m commissioner.publish` drops the skinned league sites, and where the
    # player-facing site lives. Only used for messages today; kept here so there is one place.
    "SITE_DIR": str(ROOT / "site"),
}

_dotenv_cache = None


def _load_dotenv(path=None):
    """Parse `.env` once. Missing file is fine and gives an empty dict."""
    global _dotenv_cache
    if path is None and _dotenv_cache is not None:
        return _dotenv_cache
    target = Path(path) if path else ENV_FILE
    found = {}
    if target.exists():
        for raw in target.read_text(encoding="utf-8-sig").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.lower().startswith("export "):
                line = line[7:].lstrip()
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            if key:
                found[key] = value
    if path is None:
        _dotenv_cache = found
    return found


def reload():
    """Forget the cached `.env` (useful after editing it in a long-running Flask app)."""
    global _dotenv_cache
    _dotenv_cache = None
    return _load_dotenv()


def get(key, default=None):
    """One setting, env first, then `.env`, then the built-in default."""
    if key in os.environ and os.environ[key] != "":
        return os.environ[key]
    dot = _load_dotenv()
    if dot.get(key):
        return dot[key]
    if default is not None:
        return default
    return _DEFAULTS.get(key, "")


def supabase_url():
    """No trailing slash, so callers can join paths without doubling up."""
    return get("SUPABASE_URL").rstrip("/")


def supabase_service_key():
    return get("SUPABASE_SERVICE_KEY")


def timeout():
    try:
        return float(get("SUPABASE_TIMEOUT"))
    except (TypeError, ValueError):
        return 20.0


def is_configured():
    return bool(supabase_url() and supabase_service_key())


def describe():
    """A safe-to-print summary. The service key is never shown, only its shape."""
    key = supabase_service_key()
    return {
        "env_file": str(ENV_FILE),
        "env_file_exists": ENV_FILE.exists(),
        "SUPABASE_URL": supabase_url() or "(unset)",
        "SUPABASE_SERVICE_KEY": f"(set, {len(key)} chars)" if key else "(unset)",
        "configured": is_configured(),
    }


if __name__ == "__main__":  # pragma: no cover - a convenience, not a test
    for k, v in describe().items():
        print(f"{k:24} {v}")
