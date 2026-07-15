"""Which species the import page still considers unmapped.

Regression cover for a bug where a species the fuzzy matcher had no suggestion for was
not counted as pending: it lives in state["unmapped_species"] and never becomes a key of
state["species_mappings"], so any check that consulted only that dict concluded there was
nothing left to map. In the UI that hid the "Use original names" / "Ignore unmapped"
buttons until the user set that species to "" once, and left Import enabled while the
species' rows were being dropped from the import.
"""

from review_app.app.pages.model_import._helpers import pending_species


def test_species_with_no_suggestion_is_pending_before_the_user_touches_it():
    # The reported case: 'kleiner blauer vogel' has no fuzzy match, so validation reports
    # it as unmapped without putting it in the mappings dict.
    assert pending_species({"kleiner blauer vogel"}, {}) == ["kleiner blauer vogel"]


def test_species_explicitly_cleared_is_pending():
    assert pending_species({"deer"}, {"deer": ""}) == ["deer"]


def test_mapped_species_is_not_pending():
    assert pending_species({"deer"}, {"deer": "cervus"}) == []


def test_species_mapped_to_ignore_is_not_pending():
    from review_app.backend.provider.import_service import IGNORE_SENTINEL

    assert pending_species({"deer"}, {"deer": IGNORE_SENTINEL}) == []


def test_mixed_state_reports_only_the_unresolved_ones():
    all_species = {"resolved", "cleared", "never_seen"}
    mappings = {"resolved": "cervus", "cleared": ""}
    assert pending_species(all_species, mappings) == ["cleared", "never_seen"]


def test_result_is_sorted_so_it_matches_the_row_order_shown():
    assert pending_species({"zebra", "aardvark"}, {}) == ["aardvark", "zebra"]


def test_no_species_means_nothing_pending():
    assert pending_species(set(), {}) == []


def test_none_mappings_is_treated_as_empty():
    assert pending_species({"deer"}, None) == ["deer"]


def test_mappings_for_species_not_in_the_set_are_ignored():
    """The set is authoritative; a stale mapping left over from another CSV cannot make
    a species pending, nor mask one."""
    assert pending_species({"deer"}, {"deer": "", "leftover_from_before": ""}) == ["deer"]
