"""Names shared by assignment exports and the assignment screens."""

import re
from datetime import datetime, timezone
from hashlib import sha256


def assignment_reference(assignment_id: str, number: int | None) -> str:
    if number is not None:
        return f"{number:03d}"
    # Older packages have no project sequence. Both sides can recover the same reference.
    return "L" + sha256(assignment_id.encode()).hexdigest()[:10].upper()


def _filename_part(value: str, fallback: str) -> str:
    # Bound the UTF-8 length so names fit common filesystem component limits.
    safe = re.sub(r"[^\w.-]+", "_", value).strip("._-")
    return safe.encode()[:60].decode(errors="ignore").rstrip("._-") or fallback


def package_stem(assignment, *, exported_at: datetime | None = None) -> str:
    parts = [_filename_part(assignment["project_name"], "project"), assignment["stage"]]
    if assignment["target_species"]:
        parts.append(_filename_part(assignment["target_species"], "target"))
    parts.extend(
        (
            _filename_part(assignment["recipient"], "annotator"),
            assignment["assignment_reference"],
        )
    )
    if exported_at is None:
        time = datetime.fromisoformat(assignment["created_at"])
        direction = "setup"
    else:
        time = exported_at
        direction = "return"
    if time.tzinfo is None:
        time = time.replace(tzinfo=timezone.utc)
    parts.extend((direction, time.astimezone(timezone.utc).strftime("%Y%m%d-%H%M%SZ")))
    return "-".join(parts)


def footage_folder(number: int) -> str:
    return f"Footage-{number:02d}"
