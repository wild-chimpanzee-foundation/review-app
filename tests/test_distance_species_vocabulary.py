"""Task species remain readable and immutable outside the project vocabulary."""

from unittest.mock import Mock

import pytest
from nicegui import ui
from nicegui.testing import User
from review_app.app import translations as translations_module
from review_app.app.pages.distance_review import page as page_module
from review_app.app.pages.distance_review.page import DistanceReviewPage
from sqlalchemy import text

pytest_plugins = ["nicegui.testing.user_plugin"]


@pytest.fixture
def distance_species_page(distance_data, monkeypatch):
    data, dp = distance_data, distance_data["dp"]
    dp.add_custom_species("lynx", "Lynx", "Lynx", "Mammal", "Mammifère")
    with dp.engine.connect() as connection:
        lynx_id = connection.execute(
            text("SELECT id FROM species WHERE scientific_name='lynx'")
        ).scalar_one()
    dp.update_manual_review(
        data["videos"][0], [{"species": "deer"}, {"species": "lynx"}], is_blank=False
    )
    tasks = [
        row
        for row in dp.get_distance_video_queue(data["project"].id)
        if row["video_id"] == data["videos"][0]
    ]
    lynx_task = next(row for row in tasks if row["species_id"] == lynx_id)
    second = dp.create_distance_individual(
        lynx_task["task_id"], lynx_id, 1000, anchor=data["anchor"]
    )
    dp.set_project_species(data["project"].id, ["fox"])
    monkeypatch.setattr(page_module, "get_language", lambda: "fr")
    monkeypatch.setattr(page_module, "get_annotator_name", lambda: "alice")
    monkeypatch.setattr(translations_module, "get_language", lambda: "fr")
    holder = {}

    @ui.page("/distance-species-vocabulary-test")
    async def show():
        page = DistanceReviewPage(
            dp, data["project"].id, dp.get_distance_species_options(data["project"].id, "fr"), []
        )
        page.queue = tasks
        page.index = tasks.index(lynx_task)
        page.individuals = dp.list_distance_individuals(page.task_id)
        await page.load_saved_species_options()
        page.individual_id = second["id"]
        page.render_individuals()
        holder.update(page=page, second=second)

    return holder, data


async def test_saved_species_stays_readable_and_fixed_per_task(user: User, distance_species_page):
    holder, data = distance_species_page
    await user.open("/distance-species-vocabulary-test")
    page, second = holder["page"], holder["second"]
    lookup = Mock(side_effect=AssertionError("render must use prefetched options"))
    page.dp.get_distance_species_options = lookup
    assert list(page.species_options.values()) == ["fox"]
    assert page.default_species() == second["species_id"]
    assert page.target_species_options() == {second["species_id"]: "Lynx (lynx)"}
    assert "hors de la liste des espèces du projet" in page.individual_label(page.individual)
    assert page.individual["individual_number"] == 1
    assert len(page.individuals) == 1
    # Species is a read-only label; remaining selects edit attributes/tags only.
    assert not any(
        isinstance(element, ui.select) and second["species_id"] in element.options
        for element in user.client.elements.values()
    )
    with page.client:
        page.render_individuals.refresh()
    lookup.assert_not_called()
    assert (
        data["dp"].list_distance_individuals(data["individual"]["task_id"])[0]["species_id"]
        == data["species_id"]
    )
