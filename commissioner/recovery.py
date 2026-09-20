"""Durable recovery evidence. Missing is clean; unreadable is never clean."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def read(path):
    try:
        state = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(state, dict) or not state.get("started_at"):
            raise ValueError("invalid recovery journal")
        if not isinstance(state.get("unconfirmed", []), list):
            raise ValueError("invalid unconfirmed list")
        return state
    except FileNotFoundError:
        return None
    except (OSError, ValueError, UnicodeError) as exc:
        return {"started_at": "an unknown time", "phase": "unreadable journal",
                "recovery_required": True, "read_error": type(exc).__name__}


def write(path, state):
    """Flush a complete replacement before replacing the old journal; failures propagate."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(state, stream, indent=1)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass
