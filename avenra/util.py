import hashlib
import json
import re
import uuid
from datetime import datetime, timezone

from .errors import require


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def uid(prefix):
    return prefix + "_" + uuid.uuid4().hex[:20]


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def application_fingerprint():
    """Identify owned application sources attached to a verification receipt."""
    from pathlib import Path
    root = Path(__file__).resolve().parent
    result = hashlib.sha256()
    dependency_file = root.parent / 'requirements.txt'
    result.update(b'requirements.txt\0' + dependency_file.read_bytes() + b'\0')
    for source_root in [root, root.parent / 'avenra_fidelity']:
        for path in sorted(source_root.rglob('*')):
            if path.is_file() and path.suffix in {'.py', '.sql', '.js', '.css', '.html'}:
                result.update(path.relative_to(root.parent).as_posix().encode() + b'\0' + path.read_bytes() + b'\0')
    return result.hexdigest()


def money(value, allow_zero=False):
    require(type(value) is int, "INVALID_AMOUNT", "Use an integer amount in paise.")
    require((0 if allow_zero else 1) <= value <= 100_000_000,
            "INVALID_AMOUNT", "Amount exceeds the selected local profile or is not positive.")
    return value


def label(value, field="Name", maximum=120):
    require(isinstance(value, str) and 1 <= len(value.strip()) <= maximum,
            "INVALID_FIELD", f"{field} must contain 1–{maximum} characters.")
    require(not any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in value), "INVALID_FIELD", f"{field} contains control characters or invalid Unicode.")
    return value.strip()


def reference(value):
    value = label(value, "Request key", 160)
    require(bool(re.fullmatch(r"[A-Za-z0-9_.:@/-]+", value)), "INVALID_KEY", "Request key contains unsupported characters.")
    return value


def row(value):
    return dict(value) if value is not None else None
