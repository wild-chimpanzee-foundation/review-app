"""Display labels for assigned footage camera and chunk groups."""


def format_camera_labels(camera_ids, fallback=""):
    """Show camera/chunk identifiers while retaining the existing chunk label."""
    cameras = ", ".join(camera_ids)
    return " · ".join(value for value in (fallback, cameras) if value)
