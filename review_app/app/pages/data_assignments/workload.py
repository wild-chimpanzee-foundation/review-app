"""Display labels for assigned footage camera and chunk groups."""

from review_app.app.translations import t


def format_camera_labels(camera_ids, fallback=""):
    """Show camera/chunk identifiers while retaining the existing chunk label."""
    cameras = ", ".join(camera_ids)
    return " · ".join(value for value in (fallback, cameras) if value)


def workload_labels(workload, *, video_label=None, camera_names=""):
    video_count = workload["video_count"]
    labels = [
        video_label or t("assign_video_count", count=video_count),
        t("assign_camera_count", count=workload["camera_count"]),
    ]
    if camera_names:
        labels.append(camera_names)
    labels.append(t("assign_hours", hours=f"{workload['hours']:.2f}"))
    unknown = video_count - workload["duration_count"]
    if unknown:
        labels.append(t("assign_unknown_durations", count=unknown))
    return labels
