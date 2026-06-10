def test_export_import_bundle_preserves_assignments(provider_with_project):
    dp, project, _ = provider_with_project

    # Setup assignments
    dp.add_annotator("alice")
    dp.apply_distribution(project.id, {"alice": ["cam_x"]})

    # Verify assignment exists
    assert dp.get_camera_assignment_map(project.id)["cam_x"] == "alice"

    # Export bundle including metadata
    bundle_bytes = dp.export_project_bundle(project.id, include=["metadata"])

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
