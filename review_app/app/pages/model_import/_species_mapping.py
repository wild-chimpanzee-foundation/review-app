"""The species-mapping editor shared by the import pages.

Every import page has the same problem: the CSV names species that the project's catalog
does not recognise, and the user has to say what each one means before those rows can be
imported. What differs is which answers a page can offer — only the annotations import can
register a new species in the catalog, only it understands "this means the video is
blank", and only the model import gates its own button rather than an Apply button here.

Those differences used to live as four loose keyword arguments repeated at each call site,
which made it easy to miss that one site offered no way at all to resolve an unmatched
species. SpeciesMappingOptions names them once per site instead, so the sites can be
compared at a glance — see the presets at the bottom of this module.
"""

from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass
from typing import Callable, Iterable

from nicegui import ui

from review_app.app.state import get_language
from review_app.app.translations import t
from review_app.backend.provider.import_service import BLANK_SENTINEL, IGNORE_SENTINEL

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SpeciesMappingOptions:
    """How one import site's species-mapping editor behaves.

    A species the user cannot map to an existing catalog entry has to be resolvable some
    other way, or its rows are dropped from the import without the user ever choosing
    that. So a site should enable at least one of `allow_ignore` or `allow_add_new` —
    doubly so if its import is gated on nothing being pending, since otherwise an
    unmatchable species is a dead end.
    """

    mappings_state_key: str
    """Where this site keeps its {original species: target} decisions in page state."""

    allow_ignore: bool = False
    """Offer "— ignore —", and a button to apply it to everything still pending.
    Resolves to IGNORE_SENTINEL; the importer drops those rows."""

    allow_blank: bool = False
    """Offer "— mark video blank —". Resolves to BLANK_SENTINEL, which only the
    annotations importer understands."""

    allow_add_new: bool = False
    """Offer keeping the species' own name and adding it to the project.

    Only for sites whose import actually performs that registration: the annotations
    importer collects such targets as `species_to_add` and creates them. The model
    importer does not, so enabling this there would write a name into
    model_annotations.value_text that exists in no catalog, and which then shows up as
    its own entry in the review page's species filter."""

    show_apply_button: bool = True
    """Whether the editor renders its own Apply button. Sites that re-validate on every
    change (the model import) have nothing for it to do."""


def pending_species(all_species: Iterable[str], mappings: dict[str, str] | None) -> list[str]:
    """Which of `all_species` still need a mapping decision from the user, sorted.

    Ask with the full species set, never with just the keys of the mappings dict: a
    species the fuzzy matcher had no suggestion for is only ever recorded in
    state["unmapped_species"], so it is absent from that dict until the user touches it.
    Reading the dict alone therefore reported "nothing pending" for exactly the species
    that most needed attention — hiding the bulk-resolve buttons and leaving the import
    button enabled while those rows were being silently dropped."""
    current = mappings or {}
    return [s for s in sorted(all_species) if not current.get(s)]


async def _call_maybe_async(fn: Callable) -> None:
    result = fn()
    if inspect.isawaitable(result):
        await result


def render_species_mappings(
    dp,
    state: dict,
    all_mappings: dict,
    unmapped_origs: set,
    all_species: set,
    apply_fn: Callable,
    on_change: Callable,
    can_apply: bool,
    options: SpeciesMappingOptions,
    project_id: str | None = None,
    species_counts: dict[str, int] | None = None,
) -> None:
    """Render the editor for `all_species`, writing decisions into page state.

    `on_change` is awaited after every decision, so the page can re-validate and refresh
    its counts; it may be sync or async.
    """
    key = options.mappings_state_key

    ui.label(t("species_mappings")).classes("text-subtitle1 font-weight-medium q-mb-sm")
    ui.label(t("edit_mappings_desc")).classes("text-caption q-mb-md")

    species_map = dp.get_species_display_map(get_language(), project_id)
    blank_opt = {BLANK_SENTINEL: f"— {t('map_to_blank_video')} —"} if options.allow_blank else {}
    ignore_opt = {IGNORE_SENTINEL: f"— {t('map_to_ignore')} —"} if options.allow_ignore else {}
    # Keyed by the species' own name, so choosing it maps the name to itself — which is
    # what tells the annotations importer to register it.
    add_new_opt = (
        {s: t("add_as_new_species", name=s) for s in all_species} if options.allow_add_new else {}
    )
    select_options = {
        "": "",
        **blank_opt,
        **ignore_opt,
        **add_new_opt,
        **species_map,
    }

    for orig in sorted(all_species):
        current_mapping = all_mappings.get(orig, "")
        is_unmapped = orig in unmapped_origs
        with ui.row().classes("w-full items-center q-mb-sm"):
            count = species_counts.get(orig) if species_counts else None
            count_suffix = f" ({count})" if count is not None else ""
            ui.label(f"{orig}{count_suffix}").classes(
                f"col {'text-negative' if is_unmapped else ''}"
            )
            safe_value = current_mapping if current_mapping in select_options else ""
            select = ui.select(
                label=t("mapped_to"),
                options=select_options,
                value=safe_value,
                with_input=True,
            ).props("outlined dense class=col-4")

            def make_update_fn(o: str, sel) -> Callable:
                async def _update():
                    mappings = state.get(key) or {}
                    mappings[o] = sel.value
                    state[key] = mappings
                    # The user-action trail: pairs up with the revalidation that follows,
                    # so a field log shows what was mapped and how long the page took.
                    logger.info("Species mapping changed: %r -> %r", o, sel.value)
                    await _call_maybe_async(on_change)

                return _update

            select.on_value_change(make_update_fn(orig, select))

    pending_unmapped = pending_species(all_species, state.get(key))

    with ui.row().classes("items-center gap-sm q-mt-sm"):
        apply_btn = (
            ui.button(t("apply"), icon="refresh", on_click=apply_fn, color="primary").props(
                f"wide {'disabled' if not can_apply else ''}"
            )
            if options.show_apply_button
            else None
        )

        async def ignore_unmapped():
            mappings = state.get(key) or {}
            for orig in all_species:
                if not mappings.get(orig):
                    mappings[orig] = IGNORE_SENTINEL
            state[key] = mappings
            await _call_maybe_async(on_change)
            if apply_btn:
                apply_btn.props("wide")

        if pending_unmapped and options.allow_ignore:
            ui.button(t("ignore_unmapped"), icon="block", on_click=ignore_unmapped).props(
                "flat dense color=grey"
            )
    if pending_unmapped:
        ui.label(t("map_all_to_import", list=", ".join(pending_unmapped))).classes(
            "text-warning text-caption q-mt-xs"
        )


# ── Per-site configuration ────────────────────────────────────────────────────
#
#                        ignore   blank   add new   apply button
#   model import           yes      -        -           -
#   annotations, app       yes      -       yes         yes
#   annotations, external   -      yes       -          yes
#
# The external-format site is the odd one out: it offers no way to resolve a species that
# matches no catalog entry, so such a species stays pending and its rows are dropped from
# the import. Its Apply button is only reachable because that site still gates on the
# mappings dict rather than on pending_species(). Turning on allow_ignore (and probably
# allow_add_new, since unfamiliar species are most likely here) is what would let it be
# gated properly.

MODEL_IMPORT = SpeciesMappingOptions(
    mappings_state_key="species_mappings",
    allow_ignore=True,
    # The page re-validates on every change and drives its own Import button.
    show_apply_button=False,
)

ANNOTATIONS_APP_FORMAT = SpeciesMappingOptions(
    mappings_state_key="app_species_mappings",
    allow_ignore=True,
    allow_add_new=True,
)

ANNOTATIONS_EXTERNAL_FORMAT = SpeciesMappingOptions(
    mappings_state_key="ann_species_mappings",
    allow_blank=True,
)
