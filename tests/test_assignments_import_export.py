import io
import zipfile

import pytest
from review_app.backend.annotation_package.detection import (
    detect_setup_package,
    inspect_legacy_package,
)


@pytest.mark.parametrize("include_manifest", [True, False])
def test_export_import_bundle_preserves_assignments(provider_with_project, include_manifest):
    dp, project, _ = provider_with_project

    # Setup assignments
    dp.add_annotator("alice")
    dp.apply_distribution(project.id, {"alice": ["cam_x"]})

    # Verify assignment exists
    assert dp.get_camera_assignment_map(project.id)["cam_x"] == "alice"

    # Export bundle including metadata
    bundle_bytes = dp.export_project_bundle(project.id, include=["metadata"])
    if not include_manifest:
        csv_only = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(bundle_bytes)) as source:
            with zipfile.ZipFile(csv_only, "w") as target:
                target.writestr("metadata.csv", source.read("metadata.csv"))
        bundle_bytes = csv_only.getvalue()
    assert detect_setup_package(bundle_bytes) == "legacy"
    assert inspect_legacy_package(bundle_bytes)["metadata_count"] == 2

    # Clear assignments to test restoration
    with dp.engine.begin() as conn:
        from sqlalchemy import text

        conn.execute(text("DELETE FROM video_assignments"))

    assert dp.get_camera_assignment_map(project.id)["cam_x"] is None

    # Import bundle
    dp.import_project_bundle(project.id, bundle_bytes)

    # Verify assignment is restored
    assert dp.get_camera_assignment_map(project.id)["cam_x"] == "alice", (
        "Assignment should be preserved in bundle import (via metadata.csv)"
    )


def test_export_import_annotations_csv_preserves_assignments(provider_with_project):
    dp, project, _ = provider_with_project

    # Setup assignments
    dp.add_annotator("alice")
    dp.apply_distribution(project.id, {"alice": ["cam_x"]})

    # Verify assignment exists
    assert dp.get_camera_assignment_map(project.id)["cam_x"] == "alice"

    # Export annotations CSV
    df = dp.export_annotations_csv(project.id)

    # Clear assignments
    with dp.engine.begin() as conn:
        from sqlalchemy import text

        conn.execute(text("DELETE FROM video_assignments"))

    assert dp.get_camera_assignment_map(project.id)["cam_x"] is None

    # Import annotations CSV
    dp.import_annotations_csv(df, project.id)

    # Verify assignment is restored
    assert dp.get_camera_assignment_map(project.id)["cam_x"] == "alice", (
        "Assignment should be preserved in annotations CSV import"
    )
