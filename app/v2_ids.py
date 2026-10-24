"""Backward-compatible derived Identifiers with bounded, tuple-bound SHA-256 IDs."""
import hashlib
import json

from pydantic import TypeAdapter

from .contracts import Identifier

_IDENTIFIER = TypeAdapter(Identifier)


def derived_id(*parts):
    """Keep legacy joins up to 96 characters; hash all parts, including boundaries.

    Callers retain the complete original parts in their existing metadata. The
    full digest avoids truncation collisions between equal readable prefixes.
    """
    if not parts:
        raise ValueError('A derived ID needs at least one Identifier')
    for part in parts:
        _IDENTIFIER.validate_python(part)
    legacy = '_'.join(parts)
    if len(legacy) <= 96:
        return legacy
    digest = hashlib.sha256(json.dumps(parts, separators=(',', ':')).encode('utf-8')).hexdigest()
    return legacy[:31] + '_' + digest
