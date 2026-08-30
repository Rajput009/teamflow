"""JSON-safe conversion for values stored in JSONB columns (activity log
snapshots, notification payloads).

JSONB's serializer only understands JSON primitives: an Enum becomes a raw
enum object and date/datetime are not serializable at all — either crashes
the flush that writes the snapshot (destroying the legitimate change with
it). Every value entering old_value/new_value/payload passes through here.
"""
import enum
import uuid
from datetime import date, datetime


def json_safe(value):
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    return value
