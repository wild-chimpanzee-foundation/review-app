"""Species handoff tested at the agreed LocalDataProvider / serialized snapshot interface."""

import io
import json
import zipfile
from pathlib import Path

import pytest
from review_app.backend.annotation_package.archive import decode_archive, encode_archive
from review_app.backend.provider.assignment_handoff import AssignmentRequest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError


@pytest.fixture
def handout(provider_with_project):
    dp, project, root = provider_with_project
    ids = dp.get_video_queue({}, active_project_id=project.id)
    dp.update_manual_review(
        ids[0],
        [
            {
                "species": "deer",
                "count": 2,
                "tags": ["grazing"],
                "start_sec": 1.25,
                "end_sec": 5.5,
                "labeled_by": "alice",
            }
        ],
        is_blank=False,
        labeled_by="alice",
        active_project_id=project.id,
    )
    dp.set_review_later(ids[0], True)
    prepared = dp.prepare_assignment(project.id, AssignmentRequest("alice", tuple(ids)))
    return dp, project, root, ids, dp.export_assignment(prepared.id)


def by_filename(dp, project_id):
    return {
        Path(dp.get_video_detail(vid)["video_path"]).name: vid
        for vid in dp.get_video_queue({}, active_project_id=project_id)
    }


def test_return_contains_untouched_and_new_work_and_preview_does_not_apply(handout):
    dp, project, root, ids, data = handout
    (Path(root.path) / "unlisted.mp4").touch()
    opened = dp.open_assignment(data, "Alice", {root.id: root.path})
    assert len(dp.get_video_queue({}, active_project_id=opened.project_id)) == len(ids)
    untouched = dp.export_assignment_return(opened.project_id)
    preview = dp.preview_assignment_return(project.id, untouched)
    assert preview.totals["unchanged"] == 2
    original = by_filename(dp, project.id)
    local = by_filename(dp, opened.project_id)
    empty_name = Path(dp.get_video_detail(ids[1])["video_path"]).name
    dp.update_manual_review(
        local[empty_name],
        [{"species": "fox", "count": 4}],
        is_blank=False,
        labeled_by="alice",
        active_project_id=opened.project_id,
    )
    returned = dp.export_assignment_return(opened.project_id)
    manifest, context, work = decode_archive(returned)
    assert manifest["direction"] == "return"
    assert {unit["video_id"] for unit in work} == set(ids)
    assert context["predictions"] == []
    preview = dp.preview_assignment_return(project.id, returned)
    assert preview.totals["added"] == 1
    assert preview.totals["unchanged"] == 1
    assert not dp.get_video_detail(original[empty_name])["manual_selections"]
    result = dp.accept_assignment_return(project.id, returned, "keep_existing", preview.token)
    assert result.totals == preview.totals
    assert dp.get_video_detail(original[empty_name])["manual_selections"][0]["count"] == 4
    repeated = dp.preview_assignment_return(project.id, returned)
    assert repeated.totals["unchanged"] == 2
    assert repeated.totals["added"] == 0


@pytest.mark.parametrize("policy", ["keep_existing", "use_imported"])
def test_identical_return_is_a_true_noop_even_with_reordered_records(handout, policy):
    dp, project, root, _, data = handout
    opened = dp.open_assignment(data, "Alice", {root.id: root.path})
    manifest, context, work = decode_archive(dp.export_assignment_return(opened.project_id))
    returned = encode_archive(manifest, context, list(reversed(work)))
    preview = dp.preview_assignment_return(project.id, returned, policy)
    assert preview.totals["unchanged"] == 2
    # Fail on any annotation write, including timestamp churn or delete/reinsert.
    with dp.engine.begin() as conn:
        for table in ("video_labels", "individual_observations", "observation_tags", "video_tags"):
            for operation in ("INSERT", "UPDATE", "DELETE"):
                conn.exec_driver_sql(
                    f"CREATE TRIGGER reject_{table}_{operation} BEFORE {operation} ON {table} "
                    "BEGIN SELECT RAISE(ABORT, 'identical return must not write'); END"
                )
    result = dp.accept_assignment_return(project.id, returned, policy, preview.token)
    assert result.totals == preview.totals
    assert dp.preview_assignment_return(project.id, returned, policy).token == preview.token


@pytest.mark.parametrize("policy", ["keep_existing", "use_imported"])
def test_return_imports_dependencies_only_for_applied_work_and_preserves_saved_tags(
    handout, policy
):
    dp, project, root, ids, data = handout
    opened = dp.open_assignment(data, "Alice", {root.id: root.path})
    manifest, context, work = decode_archive(dp.export_assignment_return(opened.project_id))
    context["tags"].append(
        {
            "key": "returned_evidence",
            "name_en": "Returned evidence",
            "name_fr": None,
            "color": None,
            "icon": None,
            "is_custom": True,
            "individual_eligible": False,
        }
    )
    changed = next(unit for unit in work if unit["video_id"] == ids[0])
    saved_tag = {
        "key": "returned_evidence",
        "tagged_by": "new_return_author",
        "tagged_at": "2025-01-02T03:04:05.123456",
    }
    changed["tags"].append(saved_tag)
    returned = encode_archive(manifest, context, work)
    preview = dp.preview_assignment_return(project.id, returned, policy)
    dp.accept_assignment_return(project.id, returned, policy, preview.token)
    applied = policy == "use_imported"
    assert ("returned_evidence" in {row["key"] for row in dp.get_all_tags()}) == applied
    assert ("new_return_author" in dp.get_all_annotators()) == applied
    # Re-export through the same public interface to check exact saved evidence,
    # UUIDs, attribution, timestamps, and behavior links, not just display counts.
    prepared = dp.prepare_assignment(project.id, AssignmentRequest("alice", tuple(ids)))
    _, _, saved = decode_archive(dp.export_assignment(prepared.id))
    if applied:
        assert next(unit for unit in saved if unit["video_id"] == ids[0]) == changed
    else:
        assert next(unit for unit in saved if unit["video_id"] == ids[0])["tags"] == []
    assert dp.preview_assignment_return(project.id, returned, policy).totals[
        "unchanged" if applied else "unapplied"
    ] == (2 if applied else 1)


def test_repeated_returns_obey_one_whole_video_policy_and_warn_about_admin_corrections(handout):
    dp, project, root, ids, data = handout
    opened = dp.open_assignment(data, "Alice", {root.id: root.path})
    name = Path(dp.get_video_detail(ids[0])["video_path"]).name
    local = by_filename(dp, opened.project_id)[name]
    dp.update_manual_review(
        local,
        [{"species": "fox", "count": 7}],
        is_blank=False,
        labeled_by="alice",
        active_project_id=opened.project_id,
    )
    returned = dp.export_assignment_return(opened.project_id)
    keep = dp.preview_assignment_return(project.id, returned)
    assert keep.totals["unapplied"] == 1
    assert "later annotator progress" in " ".join(keep.warnings)
    dp.accept_assignment_return(project.id, returned, "keep_existing", keep.token)
    assert dp.get_video_detail(ids[0])["manual_selections"][0]["species"] == "deer"
    imported = dp.preview_assignment_return(project.id, returned, "use_imported")
    assert imported.totals["replaced"] == 1
    assert "admin corrections" in " ".join(imported.warnings)
    assert imported.totals["annotations_removed"] >= 1
    dp.accept_assignment_return(project.id, returned, "use_imported", imported.token)
    assert [
        (row["species"], row["count"]) for row in dp.get_video_detail(ids[0])["manual_selections"]
    ] == [("fox", 7)]
    repeat = dp.preview_assignment_return(project.id, returned)
    assert repeat.totals["unchanged"] == 2
    dp.update_manual_review(
        ids[0], [{"species": "fox", "count": 99}], active_project_id=project.id
    )
    keep = dp.preview_assignment_return(project.id, returned)
    dp.accept_assignment_return(project.id, returned, "keep_existing", keep.token)
    assert dp.get_video_detail(ids[0])["manual_selections"][0]["count"] == 99


def test_working_project_sync_checks_only_listed_media_and_local_writes_are_unrestricted(handout):
    dp, project, root, ids, data = handout
    opened = dp.open_assignment(data, "Alice", {root.id: root.path})
    local = by_filename(dp, opened.project_id)
    extra = Path(root.path) / "cam_x" / "unlisted.mp4"
    extra.touch()
    result = dp.sync_videos(video_dir=Path(root.path), active_project_id=opened.project_id)
    assert result["added"] == 0
    assert by_filename(dp, opened.project_id).keys() == local.keys()
    assert len(dp.get_video_queue({}, active_project_id=opened.project_id)) == len(ids)
    # Normal Species annotation remains available in the working project.
    video_id = next(iter(local.values()))
    dp.update_manual_review(
        video_id,
        [{"species": "fox", "count": 3}],
        is_blank=False,
        active_project_id=opened.project_id,
    )
    # A Species assignment still permits ordinary Distance task creation and editing.
    task = next(
        row["task_id"]
        for row in dp.get_distance_video_queue(opened.project_id)
        if row["video_id"] == video_id
    )
    dp.record_distance_task_absence(task, "alice")
    assert dp.get_distance_review_state(task, "alice")["task"]["completion_outcome"] == "absent"
    _, _, work = decode_archive(dp.export_assignment_return(opened.project_id))
    assert {unit["video_id"] for unit in work} == set(ids)


def test_open_on_another_instance_returns_complete_progress_even_when_footage_is_missing(
    handout, tmp_path, monkeypatch
):
    from review_app.backend.provider.local_data_provider import LocalDataProvider

    dp, project, root, ids, data = handout
    monkeypatch.setattr(
        "review_app.backend.provider.local_data_provider.get_user_data_dir",
        lambda: tmp_path / "other-db",
    )
    target = LocalDataProvider()
    try:
        opened = target.open_assignment(
            data, "Remote", {root.id: str(tmp_path / "not-delivered-yet")}
        )
        assert set(opened.missing_video_ids) == set(ids)
        assert target.get_project_video_count(opened.project_id) == 2
        returned = target.export_assignment_return(opened.project_id)
        manifest, _, work = decode_archive(returned)
        assert set(manifest["work_keys"]) == set(ids)
        assert len(work) == 2
        assert dp.preview_assignment_return(project.id, returned).totals["unchanged"] == 2
        located = target.get_project_dirs(opened.project_id)[0]
        target.update_project_dir(located.id, root.path)
        target.sync_videos(video_dir=Path(root.path), active_project_id=opened.project_id)
        assert len(by_filename(target, opened.project_id)) == 2
    finally:
        target.engine.dispose()


def test_empty_unfinished_return_warns_before_removing_work_but_omitted_work_never_deletes(
    handout,
):
    dp, project, root, ids, data = handout
    task = dp.get_distance_video_queue(project.id)[0]["task_id"]
    dp.record_distance_task_absence(task, "admin")
    distance_before = dp.get_distance_review_state(task, "admin")
    opened = dp.open_assignment(data, "Alice", {root.id: root.path})
    name = Path(dp.get_video_detail(ids[0])["video_path"]).name
    local = by_filename(dp, opened.project_id)[name]
    dp.update_manual_review(local, [], is_blank=None, active_project_id=opened.project_id)
    returned = dp.export_assignment_return(opened.project_id)
    manifest, context, work = decode_archive(returned)
    omitted = encode_archive(
        manifest, context, [unit for unit in work if unit["video_id"] != ids[0]]
    )
    preview = dp.preview_assignment_return(project.id, omitted, "use_imported")
    assert preview.totals["annotations_removed"] == 0
    dp.accept_assignment_return(project.id, omitted, "use_imported", preview.token)
    assert dp.get_video_detail(ids[0])["manual_selections"]
    preview = dp.preview_assignment_return(project.id, returned, "use_imported")
    changed = next(unit for unit in preview.units if unit["video_id"] == ids[0])
    assert changed["removed"] == {"labels": 1, "observations": 1, "behaviors": 1, "tags": 0}
    assert preview.totals["annotations_removed"] == 3
    assert "unfinished or empty" in " ".join(preview.warnings)
    dp.accept_assignment_return(project.id, returned, "use_imported", preview.token)
    assert dp.get_video_detail(ids[0])["manual_selections"] == []
    assert dp.get_distance_review_state(task, "admin") == distance_before
    assert dp.preview_assignment_return(project.id, returned).totals["unchanged"] == 2


def test_acceptance_requires_a_current_preview_of_payload_policy_work_and_media(handout, tmp_path):
    dp, project, root, ids, data = handout
    opened = dp.open_assignment(data, "Alice", {root.id: root.path})
    returned = dp.export_assignment_return(opened.project_id)
    preview = dp.preview_assignment_return(project.id, returned)
    with pytest.raises(ValueError, match="stale"):
        dp.accept_assignment_return(project.id, returned, "use_imported", preview.token)
    manifest, context, work = decode_archive(returned)
    context["project_name"] = "Different payload"
    changed = encode_archive(manifest, context, work)
    with pytest.raises(ValueError, match="stale"):
        dp.accept_assignment_return(project.id, changed, "keep_existing", preview.token)
    dp.update_manual_review(
        ids[0], [{"species": "deer", "count": 9}], active_project_id=project.id
    )
    with pytest.raises(ValueError, match="stale"):
        dp.accept_assignment_return(project.id, returned, "keep_existing", preview.token)
    preview = dp.preview_assignment_return(project.id, returned)
    dp.update_project_dir(root.id, str(tmp_path / "relocated"))
    with pytest.raises(ValueError, match="stale"):
        dp.accept_assignment_return(project.id, returned, "keep_existing", preview.token)
    assert dp.get_video_detail(ids[0])["manual_selections"][0]["count"] == 9


def test_returns_validate_saved_project_ownership_scope_and_dependencies_before_acceptance(
    handout,
):
    dp, project, root, ids, data = handout
    opened = dp.open_assignment(data, "Alice", {root.id: root.path})
    returned = dp.export_assignment_return(opened.project_id)
    before = dp.get_video_detail(ids[0])["manual_selections"]
    wrong_project = dp.create_project("Unrelated", "")
    with pytest.raises(ValueError, match="originating assignment"):
        dp.preview_assignment_return(wrong_project.id, returned, "use_imported")
    for member, mutate in (
        ("manifest.json", lambda value: value.update(recipient="mallory")),
        ("manifest.json", lambda value: value.update(source_project_id=wrong_project.id)),
        ("manifest.json", lambda value: value.update(stage="distance", target_species="deer")),
        ("work.json", lambda value: value[0].update(video_id="unassigned")),
        ("context.json", lambda value: value["media"][0].update(video_path="cam_x/different.mp4")),
        ("context.json", lambda value: value.update(behaviors=[])),
    ):
        output = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(returned)) as source, zipfile.ZipFile(output, "w") as dest:
            for name in source.namelist():
                value = json.loads(source.read(name))
                if name == member:
                    mutate(value)
                dest.writestr(name, json.dumps(value))
        with pytest.raises(ValueError):
            dp.preview_assignment_return(project.id, output.getvalue(), "use_imported")
        with pytest.raises(ValueError):
            dp.accept_assignment_return(project.id, output.getvalue(), "use_imported", "anything")
    with pytest.raises(ValueError, match="entire return"):
        dp.preview_assignment_return(project.id, returned, {ids[0]: "use_imported"})
    assert dp.get_video_detail(ids[0])["manual_selections"] == before


def test_late_failure_rolls_back_all_returned_work_and_new_dependencies(handout):
    dp, project, root, ids, data = handout
    opened = dp.open_assignment(data, "Alice", {root.id: root.path})
    for local in by_filename(dp, opened.project_id).values():
        dp.update_manual_review(
            local,
            [{"species": "fox", "count": 8}],
            is_blank=False,
            labeled_by="returner",
            active_project_id=opened.project_id,
        )
    manifest, context, work = decode_archive(dp.export_assignment_return(opened.project_id))
    context["tags"].append(
        {
            "key": "new_return_tag",
            "name_en": "New tag",
            "name_fr": None,
            "color": None,
            "icon": None,
            "is_custom": True,
            "individual_eligible": False,
        }
    )
    work[0]["tags"].append(
        {"key": "new_return_tag", "tagged_by": "returner", "tagged_at": "2025-01-01T00:00:00"}
    )
    returned = encode_archive(manifest, context, work)
    preview = dp.preview_assignment_return(project.id, returned, "use_imported")
    before = {vid: dp.get_video_detail(vid)["manual_selections"] for vid in ids}
    authors = dp.get_all_annotators()
    with dp.engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE reject_return (video_id TEXT)")
        conn.execute(
            text("INSERT INTO reject_return VALUES (:vid)"), {"vid": preview.units[-1]["video_id"]}
        )
        conn.exec_driver_sql("""CREATE TRIGGER reject_return_write BEFORE INSERT ON individual_observations
            WHEN NEW.video_id IN (SELECT video_id FROM reject_return)
            BEGIN SELECT RAISE(ABORT, 'simulated late write failure'); END""")
    with pytest.raises(IntegrityError, match="late write failure"):
        dp.accept_assignment_return(project.id, returned, "use_imported", preview.token)
    assert {vid: dp.get_video_detail(vid)["manual_selections"] for vid in ids} == before
    assert "new_return_tag" not in {row["key"] for row in dp.get_all_tags()}
    assert dp.get_all_annotators() == authors
    assert (
        dp.preview_assignment_return(project.id, returned, "use_imported").token == preview.token
    )


def test_open_failure_rolls_back_the_fresh_project_and_its_context(handout):
    dp, _, root, _, data = handout
    before = {row.id for row in dp.list_projects()}
    with dp.engine.begin() as conn:
        conn.exec_driver_sql("""CREATE TRIGGER reject_second_video BEFORE INSERT ON videos
            WHEN EXISTS (SELECT 1 FROM videos v WHERE v.project_id = NEW.project_id)
            BEGIN SELECT RAISE(ABORT, 'simulated open failure'); END""")
    with pytest.raises(IntegrityError, match="open failure"):
        dp.open_assignment(data, "Alice", {root.id: root.path})
    assert {row.id for row in dp.list_projects()} == before


def test_open_twice_creates_fresh_projects_with_preserved_work_and_local_identities(handout):
    dp, project, root, ids, data = handout
    (Path(root.path) / "cam_x" / "unassigned.mp4").touch()
    first = dp.open_assignment(data, "Alice", {root.id: root.path})
    second = dp.open_assignment(data, "Alice", {root.id: root.path})
    assert len({project.id, first.project_id, second.project_id}) == 3
    assert dp.get_project(first.project_id).name == "Alice"
    assert dp.get_project(second.project_id).name == "Alice (2)"
    original = by_filename(dp, project.id)
    a, b = by_filename(dp, first.project_id), by_filename(dp, second.project_id)
    assert a.keys() == b.keys() == original.keys() == {"a.mp4", "b.mp4"}
    assert not set(a.values()) & set(b.values())
    assert not set(a.values()) & set(ids)
    name = Path(dp.get_video_detail(ids[0])["video_path"]).name
    before = dp.get_video_detail(ids[0])
    after = dp.get_video_detail(a[name])
    assert after["manual_selections"] == before["manual_selections"]
    assert after["review_later"] == before["review_later"]
    assert first.missing_video_ids == ()
