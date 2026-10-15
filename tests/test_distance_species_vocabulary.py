"""Regression coverage for saved Distance species outside the current project vocabulary."""

import asyncio
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
            text("SELECT id FROM species WHERE scientific_name = 'lynx'")
        ).scalar_one()
    second = dp.create_distance_individual(data["videos"][0], lynx_id, 1000, anchor=data["anchor"])
    dp.set_project_species(data["project"].id, ["fox"])
    monkeypatch.setattr(page_module, "get_language", lambda: "fr")
    monkeypatch.setattr(page_module, "get_annotator_name", lambda: "alice")
    monkeypatch.setattr(translations_module, "get_language", lambda: "fr")
    holder = {}

    @ui.page("/distance-species-vocabulary-test")
    async def drawer():
        options = dp.get_distance_species_options(data["project"].id, "fr")
        page = DistanceReviewPage(dp, data["project"].id, options, [])
        page.queue = [{"video_id": data["videos"][0], "camera_id": "cam_x"}]
        page.index = 0
        page.individuals = dp.list_distance_individuals(data["videos"][0])
        await page.load_saved_species_options()
        page.individual_id = data["individual"]["id"]
        page.render_individuals()
        holder.update(page=page, second=second)

    return holder, data


async def wait_for_active(page, expected_id):
    for _ in range(100):
        if page.individual_id == expected_id and not page.busy:
            await asyncio.sleep(0.01)
            return
        await asyncio.sleep(0.01)
    raise AssertionError("Distance individual selection did not finish")


async def test_saved_species_options_are_per_card_and_keep_new_choices_project_scoped(
    user: User, distance_species_page
):
    holder, data = distance_species_page
    await user.open("/distance-species-vocabulary-test")
    page = holder["page"]
    deer_id, lynx_id = data["species_id"], holder["second"]["species_id"]
    provider_lookup = Mock(side_effect=AssertionError("render must use prefetched options"))
    page.dp.get_distance_species_options = provider_lookup
    page.individual_label(page.individuals[0])
    page.render_individuals.refresh()
    provider_lookup.assert_not_called()

    assert len(page.species_options) == 1
    assert set(page.species_options) != {deer_id, lynx_id}
    assert page.species_options[next(iter(page.species_options))] == "fox"

    def individual_select(individual_id):
        card = next(
            element
            for element in user.client.elements.values()
            if element.props.get("data-distance-individual") == individual_id
        )
        return next(element for element in card.descendants() if isinstance(element, ui.select))

    deer_select = individual_select(data["individual"]["id"])
    lynx_select = individual_select(holder["second"]["id"])
    assert set(deer_select.options) == {next(iter(page.species_options)), deer_id}
    assert set(lynx_select.options) == {next(iter(page.species_options)), lynx_id}
    assert "hors de la liste des espèces du projet" in deer_select.options[deer_id]
    assert "Lynx (lynx)" in lynx_select.options[lynx_id]
    assert deer_id not in lynx_select.options
    assert lynx_id not in deer_select.options

    user.find("#2").click()
    await wait_for_active(page, holder["second"]["id"])
    assert (
        page.individual_label(page.individual)
        == "#2 · Lynx (lynx) (hors de la liste des espèces du projet)"
    )
    saved_species = {
        row["id"]: row["species_id"]
        for row in data["dp"].list_distance_individuals(data["videos"][0])
    }
    assert saved_species == {
        data["individual"]["id"]: deer_id,
        holder["second"]["id"]: lynx_id,
    }
