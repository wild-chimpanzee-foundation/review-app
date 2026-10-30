"""Species tasks isolate evidence, alignment, completion, and resume state."""

import pytest
from review_app.backend.db.models import (
    DistanceReviewTask,
    IndividualObservation,
    ModelAnnotation,
    Species,
)
from sqlalchemy import text


@pytest.fixture
def task_data(provider_with_project):
    dp, project, _ = provider_with_project
    video_id = dp.get_video_queue({}, active_project_id=project.id)[0]
    with dp.Session.begin() as session:
        species = {row.scientific_name: row.id for row in session.query(Species)}
        for number, name in enumerate(("deer", "fox"), 1):
            session.add(
                IndividualObservation(
                    video_id=video_id, id=number, species_id=species[name], count=2
                )
            )
    tasks = {row["species_id"]: row for row in dp.get_distance_video_queue(project.id)}
    return dp, project, video_id, species, tasks


def create(dp, task, species):
    return dp.create_distance_individual(
        task,
        species,
        0,
        age="Unknown",
        sex="Unknown",
        labeled_by="alice",
        anchor={"x": 0.1, "y": 0.1, "width": 0.2, "height": 0.2},
    )


def test_eligibility_is_confirmed_only_and_identity_survives_label_corrections(task_data):
    dp, project, video, species, tasks = task_data
    other = next(v for v in dp.get_video_queue({}, active_project_id=project.id) if v != video)
    with dp.Session.begin() as session:
        session.add(
            ModelAnnotation(
                video_id=other, annotation_type="species", model_name="model", value_text="deer"
            )
        )
    assert dp.get_distance_video_queue(project.id) == list(tasks.values())
    with dp.Session.begin() as session:
        session.query(IndividualObservation).filter_by(video_id=video).delete()
    assert dp.get_distance_video_queue(project.id) == list(tasks.values())
    with dp.engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM video_labels")).scalar() == 0
    with pytest.raises(ValueError, match="Select a species-specific"):
        dp.align_distance_on_even_frame(video, 500, "alice")


def test_target_species_options_require_manual_annotations_not_predictions_or_old_tasks(task_data):
    dp, project, video, species, tasks = task_data
    dp.add_custom_species("lynx", "Lynx", "Lynx", "Mammal", "Mammifère")
    dp.set_project_species(project.id, ["deer", "fox", "lynx"])
    with dp.Session.begin() as session:
        session.query(IndividualObservation).filter_by(
            video_id=video, species_id=species["fox"]
        ).delete()
        session.add(
            ModelAnnotation(
                video_id=video, annotation_type="species", model_name="model", value_text="lynx"
            )
        )
    options = dp.get_distance_target_species_options(project.id)
    assert set(options) == {species["deer"]}
    # The old fox task survives label corrections, but cannot populate the selector.
    assert len(dp.get_distance_video_queue(project.id)) == len(tasks)
    assert set(dp.get_distance_species_options(project.id)) > set(options)
    with dp.Session.begin() as session:
        session.query(IndividualObservation).filter_by(video_id=video).delete()
    assert dp.get_distance_target_species_options(project.id) == {}


def test_target_species_options_are_project_scoped_and_include_manual_species_outside_vocabulary(
    task_data,
):
    from review_app.backend.db.models import Video

    dp, project, video, species, _ = task_data
    other = dp.create_project("Other project", "/other-project")
    with dp.Session.begin() as session:
        session.query(IndividualObservation).filter_by(
            video_id=video, species_id=species["fox"]
        ).delete()
        session.add(Video(video_id="other-video", project_id=other.id, video_path="other.mp4"))
        session.flush()
        session.add(
            IndividualObservation(video_id="other-video", id=1, species_id=species["fox"], count=1)
        )
    dp.set_project_species(project.id, ["fox"])
    options = dp.get_distance_target_species_options(project.id, "fr")
    assert set(options) == {species["deer"]}
    assert options == {
        species["deer"]: dp.get_distance_species_options(
            project.id, "fr", include_species_ids=[species["deer"]]
        )[species["deer"]]
    }
    assert set(dp.get_distance_target_species_options(other.id)) == {species["fox"]}


def test_alignment_reset_samples_completion_and_resume_are_task_scoped(task_data):
    dp, project, video, species, tasks = task_data
    deer, fox = (tasks[species[name]]["task_id"] for name in ("deer", "fox"))
    dp.align_distance_on_even_frame(deer, 500, "alice")
    dp.align_distance_on_even_frame(fox, 1000, "bob")
    a, b = create(dp, deer, species["deer"]), create(dp, fox, species["fox"])
    assert a["individual_number"] == b["individual_number"] == 1
    for task, individual, author in ((deer, a, "alice"), (fox, b, "bob")):
        dp.apply_distance_to_end(
            individual["id"],
            dp.get_distance_review_state(task, author)["offsets"][0],
            skip_reason="individual absent",
            labeled_by=author,
        )
        dp.set_distance_active_individual(task, author, individual["id"])
        dp.finish_distance_task(task, author)
    before = dp.get_distance_review_state(fox, "bob")
    dp.align_distance_on_even_frame(deer, 700, "alice", reset_tracks=True)
    assert dp.list_distance_individuals(deer) == []
    assert dp.get_distance_review_state(fox, "bob") == before
    assert dp.get_distance_review_state(deer, "alice")["active_individual_id"] is None
    assert dp.list_distance_individuals(fox)[0]["id"] == b["id"]
    # Another provider/session sees the same persisted task and bookmark.
    from review_app.backend.provider.local_data_provider import LocalDataProvider

    reopened = LocalDataProvider()
    assert reopened.get_distance_review_state(fox, "bob") == before
    with dp.Session() as session:
        assert session.get(DistanceReviewTask, deer).completion_history[0]["outcome"] == "present"


def test_species_and_cross_task_writes_are_rejected(task_data):
    dp, _, _, species, tasks = task_data
    deer, fox = (tasks[species[name]]["task_id"] for name in ("deer", "fox"))
    for task in (deer, fox):
        dp.align_distance_on_even_frame(task, 500, "alice")
    a = create(dp, deer, species["deer"])
    b = dp.create_distance_individual(
        fox,
        species["fox"],
        0,
        age="Infant",
        anchor={"x": 0.1, "y": 0.1, "width": 0.2, "height": 0.2},
    )
    with pytest.raises(ValueError, match="target species"):
        create(dp, deer, species["fox"])
    with pytest.raises(ValueError, match="target species"):
        dp.set_distance_individual_attributes(
            a["id"], species_id=species["fox"], age="Adult", sex="Unknown", tag_ids=[]
        )
    with pytest.raises(ValueError, match="this task"):
        dp.set_distance_active_individual(deer, "alice", b["id"])
    with pytest.raises(ValueError, match="this task"):
        dp.save_distance_sample(
            a["id"], 500, skip_reason="individual absent", infant_id=b["id"], labeled_by="alice"
        )


def test_absence_is_explicit_and_new_human_evidence_reopens_only_that_task(task_data):
    dp, _, _, species, tasks = task_data
    deer, fox = (tasks[species[name]]["task_id"] for name in ("deer", "fox"))
    with pytest.raises(ValueError, match="Review all individuals"):
        dp.finish_distance_task(deer, "alice")
    dp.record_distance_task_absence(deer, "alice")
    dp.record_distance_task_absence(fox, "bob")
    before = dp.get_distance_review_state(fox, "bob")
    assert dp.get_distance_review_state(deer, "alice")["task"]["completion_outcome"] == "absent"
    create(dp, deer, species["deer"])
    assert dp.get_distance_review_state(deer, "alice")["task"]["completed_at"] is None
    assert dp.get_distance_review_state(fox, "bob") == before
    with pytest.raises(ValueError, match="human target-species"):
        dp.record_distance_task_absence(deer, "alice")
