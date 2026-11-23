"""Display labels for assigned footage camera and chunk groups."""

from review_app.app.translations import t


def workload_labels(workload, *, camera_names=""):
    video_count = workload["video_count"]
    labels = [
        t("assign_video_count", count=video_count),
        t("assign_camera_count", count=workload["camera_count"]),
    ]
    if camera_names:
        labels.append(camera_names)
    labels.append(t("assign_hours", hours=f"{workload['hours']:.2f}"))
    unknown = video_count - workload["duration_count"]
    if unknown:
        labels.append(t("assign_unknown_durations", count=unknown))
    return labels
