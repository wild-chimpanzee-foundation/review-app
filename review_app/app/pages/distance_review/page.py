from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path

from nicegui import app, run, ui
from sqlalchemy.exc import IntegrityError

from review_app.app.config import get_user_data_dir
from review_app.app.pages.distance_review.anchor_dialogs import (
    AnchorDialogContext,
    open_create_individual_dialog,
    open_redraw_anchor_dialog,
)
from review_app.app.pages.distance_review.clock_dialog import (
    ClockDialogContext,
    open_clock_alignment_dialog,
)
from review_app.app.pages.distance_review.individuals import (
    IndividualViewsContext,
)
from review_app.app.pages.distance_review.individuals import (
    render_individual_card as render_individual_card_view,
)
from review_app.app.pages.distance_review.individuals import (
    render_individuals as render_individuals_view,
)
from review_app.app.pages.distance_review.player import (
    anchor_mode,
    capture_offset,
    configure_anchor,
    frame_tolerance_ms,
    image_filters,
    install_anchor_editor,
    install_manual_pause_snap,
    pause_programmatically,
    play_to_sample,
    playback_shortcuts,
    seek_paused,
    stop_player,
    switch_source,
)
from review_app.app.pages.distance_review.reference_media import frame_time, reference_frame_offset
from review_app.app.pages.distance_review.reference_views import (
    ReferenceViewsContext,
)
from review_app.app.pages.distance_review.reference_views import (
    render_frames as render_reference_frames,
)
from review_app.app.pages.distance_review.reference_views import (
    render_reference_controls as render_reference_controls_view,
)
from review_app.app.pages.distance_review.reference_views import (
    render_references as render_references_view,
)
from review_app.app.pages.distance_review.sample_controls import (
    ApplyToEndDialogContext,
    SampleControlsContext,
    open_apply_to_end_dialog,
    render_sample_controls,
)
from review_app.app.pages.distance_review.sample_controls import (
    render_sample_actions as render_sample_actions_view,
)
from review_app.app.pages.distance_review.session import DraftState, SampleSelection
from review_app.app.pages.distance_review.tracks import track_options
from review_app.app.pages.review.annotations import _shortcut_badge
from review_app.app.pages.review.video_player import render_custom_video_player
from review_app.app.state import (
    get_active_project_id,
    get_annotator_name,
    get_distance_queue_state,
    get_language,
    set_distance_queue_state,
)
from review_app.app.translations import t
from review_app.app.utils import get_or_create_data_provider
from review_app.backend.distance_timing import (
    sample_camera_time,
    sample_frame_time,
)
from review_app.backend.path_matching import normalize_path_str
from review_app.backend.provider.video import _probe_frame_rate, _probe_video
from review_app.backend.transcoding import inspect_transcoded_cache
from review_app.backend.utils import generate_thumbnail

logger = logging.getLogger(__name__)

SKIP_CHOICES = (
    {
        "id": "midpoint not visible",
        "skip_reason": "midpoint not visible",
        "label": "distance_midpoint_hidden",
    },
    {"id": "individual absent", "skip_reason": "individual absent", "label": "distance_absent"},
)


def reference_path(reference: dict) -> Path:
    cache = inspect_transcoded_cache(reference.get("transcoded_path"), validate_contents=False)
    path = reference["transcoded_path"] if cache.current else reference["video_path"]
    return Path(normalize_path_str(path))


def stable_client(callback):
    @wraps(callback)
    async def guarded(self, *args, **kwargs):
        with self.client:
            return await callback(self, *args, **kwargs)

    return guarded


class DistanceReviewPage:
    def __init__(self, dp, project_id: str, species_options: dict[str, str], tags: list[dict]):
        self.dp, self.project_id = dp, project_id
        self.species_options = species_options
        self.reviewed_species: list[str] = []
        self.reviewed_species_counts: list[dict] = []
        self.all_tag_options = {
            tag["id"]: tag.get(f"name_{get_language()}") or tag["name_en"] for tag in tags
        }
        self.tag_options = {
            tag["id"]: self.all_tag_options[tag["id"]]
            for tag in tags
            if tag["individual_eligible"]
        }
        self.queue: list[dict] = []
        self.index = 0
        saved_queue = get_distance_queue_state(project_id)
        self.species_filter: str | None = saved_queue.get("species_filter")
        if self.species_filter not in species_options:
            self.species_filter = None
        self.camera_filter: str | None = saved_queue.get("camera_filter")
        self.status_filter: str | None = saved_queue.get("status_filter")
        if self.status_filter not in ("not_started", "in_progress", "completed"):
            self.status_filter = None
        self.camera_options: list[str] = []
        self.client = ui.context.client
        self.media_urls: dict[Path, str] = {}
        self.thumbnail_tasks: dict[Path, asyncio.Task] = {}
        self.animal_frame_rate: float | None = None
        self.animal_frame_rates: dict[Path, float | None] = {}
        self.client.on_delete(self.release_media)
        self.right_drawer = None
        self.individuals: list[dict] = []
        self.saved_species_options: dict[str, str] = {}
        self.individual_drafts: dict[str, dict] = {}
        self.individual_attributes_lock = asyncio.Lock()
        self.individual_id: str | None = None
        self.references: list[dict] = []
        self.reference: dict | None = None
        self.reference_duration = 0.0
        self.frames: list[dict] = []
        self._selection: SampleSelection | None = None
        self.animal_player = self.reference_player = None
        self.busy = False
        self.review: dict | None = None
        self.offsets: list[int] = []
        self.samples: list[dict] = []
        self.sample_offset: int | None = None
        self.behavior_options: dict[str, str] = {}
        self._draft_state = DraftState()

    @property
    def selected_frame_id(self) -> str | None:
        """Compatibility surface for older callers; selection kind stays explicit."""
        return self._selection.value if self._selection else None

    @selected_frame_id.setter
    def selected_frame_id(self, value: str | None) -> None:
        visibility_ids = {choice["id"] for choice in SKIP_CHOICES}
        self._selection = SampleSelection.from_legacy_id(value, visibility_ids)

    @property
    def sample_drafts(self) -> dict[tuple[str, int], dict]:
        return self._draft_state.drafts

    @sample_drafts.setter
    def sample_drafts(self, value: dict[tuple[str, int], dict]) -> None:
        if not hasattr(self, "_draft_state"):
            self._draft_state = DraftState()
        self._draft_state.drafts = value

    @property
    def sample_defaults(self) -> dict[str, dict]:
        return self._draft_state.defaults

    @sample_defaults.setter
    def sample_defaults(self, value: dict[str, dict]) -> None:
        if not hasattr(self, "_draft_state"):
            self._draft_state = DraftState()
        self._draft_state.defaults = value

    def media_url(self, path: Path) -> str:
        """Keep media routes alive across player refreshes and independent of other tabs."""
        path = path.resolve()
        if path not in self.media_urls:
            self.media_urls[path] = app.add_media_file(
                local_file=path,
                url_path=f"/distance-media/{self.client.id}/{uuid.uuid4()}",
            )
        return self.media_urls[path]

    def release_media(self) -> None:
        for task in self.thumbnail_tasks.values():
            task.cancel()
        for url in self.media_urls.values():
            app.remove_route(url)
        self.media_urls.clear()

    def client_handler(self, callback):
        """Keep asynchronous callbacks alive when they refresh their originating slot."""

        @wraps(callback)
        async def guarded(*args, **kwargs):
            with self.client:
                return await callback(*args, **kwargs)

        return guarded

    def refresh_sample_ui(self) -> None:
        self.render_samples.refresh()
        self.render_sample_actions.refresh()
        self.render_individual_progress.refresh()
        self.render_tracks.refresh()

    def remember_sample_choices(self) -> None:
        draft = self.sample_drafts.get((self.individual_id, self.sample_offset))
        if draft is None:
            return
        draft["reference_frame_id"] = None if self.selected_skip_reason else self.selected_frame_id
        draft["skip_reason"] = self.selected_skip_reason
        draft["reference_video_id"] = self.reference["id"] if self.reference else None
        self._draft_state.remember(self.individual_id, draft)

    def current_sample_draft(self) -> dict:
        defaults = self.sample_defaults.get(self.individual_id)
        if self.sample or defaults is None:
            source = self.sample or self.reference_sample
            children = [
                row
                for row in self.samples
                if source
                and row["carried_by_individual_id"] == self.individual_id
                and row["offset_ms"] == source["offset_ms"]
            ]
            defaults = {
                "behavior_ids": list(source["behavior_ids"]) if source else [],
                "carries": bool(children),
                "infant_id": children[0]["individual_id"] if children else "_new",
            }
        draft = self._draft_state.draft(
            key=(self.individual_id, self.sample_offset),
            default=defaults,
            selection_id=self.selected_frame_id,
            skip_reason=self.selected_skip_reason,
            reference_video_id=self.reference["id"] if self.reference else None,
        )
        if draft["infant_id"] != "_new" and not any(
            row["id"] == draft["infant_id"]
            and row["age"] == "Infant"
            and row["species_id"] == self.individual["species_id"]
            for row in self.individuals
        ):
            draft["carries"], draft["infant_id"] = False, "_new"
        if self.individual["age"] == "Infant":
            draft["carries"] = False
        return draft

    @property
    def selected_skip_reason(self) -> str | None:
        return next(
            (
                choice["skip_reason"]
                for choice in SKIP_CHOICES
                if choice["id"] == self.selected_frame_id
            ),
            None,
        )

    @property
    def sample(self) -> dict | None:
        return next(
            (
                row
                for row in self.samples
                if row["individual_id"] == self.individual_id
                and row["offset_ms"] == self.sample_offset
            ),
            None,
        )

    def reviewed_offsets(self) -> set[int]:
        return {
            row["offset_ms"] for row in self.samples if row["individual_id"] == self.individual_id
        }

    @property
    def reference_sample(self) -> dict | None:
        """Restore saved evidence, or the last known distance when resuming a new stop."""
        if self.sample and (
            self.sample.get("reference_frame_id") or self.sample.get("skip_reason")
        ):
            return self.sample
        return max(
            (
                row
                for row in self.samples
                if row["individual_id"] == self.individual_id
                and self.sample_offset is not None
                and row["offset_ms"] < self.sample_offset
                and (row.get("reference_frame_id") or row.get("skip_reason"))
            ),
            key=lambda row: row["offset_ms"],
            default=None,
        )

    def ensure_reference_selection(self) -> None:
        if self.selected_skip_reason:
            return
        if not any(frame["id"] == self.selected_frame_id for frame in self.frames):
            first = min(self.frames, key=lambda frame: frame["distance_m"], default=None)
            self.selected_frame_id = first["id"] if first else None

    @stable_client
    async def reload_review(self, *, resume: bool = False) -> None:
        state = await run.io_bound(
            self.dp.get_distance_review_state, self.video["video_id"], get_annotator_name()
        )
        self.review, self.offsets, self.samples = (
            state["review"],
            state["offsets"],
            state["samples"],
        )
        if self.review and self.video.get("video_path"):
            await self.load_sample_timing()
        if resume:
            active = state["active_individual_id"]
            if any(row["id"] == active for row in self.individuals):
                self.individual_id = active
            self.resume_sample()

    async def load_sample_timing(self) -> None:
        video = self.video
        path = Path(normalize_path_str(video["video_path"]))
        cache = inspect_transcoded_cache(video.get("transcoded_path"), validate_contents=False)
        if cache.current:
            path = Path(video["transcoded_path"])
        if path not in self.animal_frame_rates:
            self.animal_frame_rates[path] = await run.io_bound(
                _probe_frame_rate, path, timeout_sec=2
            )
        self.animal_frame_rate = self.animal_frame_rates[path]

    def sample_frame_offset(self, offset_ms: int) -> float:
        if self.review and self.review.get("clock_anchor_playback_ms") is not None:
            if offset_ms == 0 and self.review.get("opening_even_second"):
                return 0.0
            anchor = self.review["clock_anchor_playback_ms"]
            duration = self.video.get("duration_sec")
            remaining = duration - anchor / 1000 if duration is not None else None
            return round(
                anchor
                + sample_frame_time(
                    offset_ms - self.review["clock_anchor_offset_ms"],
                    self.animal_frame_rate,
                    remaining,
                )
                * 1000,
                9,
            )
        return round(
            sample_frame_time(offset_ms, self.animal_frame_rate, self.video.get("duration_sec"))
            * 1000,
            9,
        )

    def sample_seek_offset(self, offset_ms: int) -> float:
        frame_ms = self.sample_frame_offset(offset_ms)
        # Seek ten microseconds inside the chosen frame to avoid browser rounding
        # to its predecessor. This does not move to a later frame.
        padding_ms = 0.01
        duration = self.video.get("duration_sec")
        if duration is not None and duration * 1000 > frame_ms:
            padding_ms = min(padding_ms, (duration * 1000 - frame_ms) / 2)
        return frame_ms + padding_ms if frame_ms else 0

    def resume_sample(self) -> None:
        reviewed = self.reviewed_offsets()
        self.sample_offset = next(
            (offset for offset in self.offsets if offset not in reviewed),
            self.offsets[0] if self.offsets else None,
        )

    @property
    def video(self) -> dict | None:
        return self.queue[self.index] if self.queue else None

    @property
    def individual(self) -> dict | None:
        return next((row for row in self.individuals if row["id"] == self.individual_id), None)

    @stable_client
    async def load_video(self) -> None:
        stop_player(self.animal_player)
        stop_player(self.reference_player)
        self.individuals = []
        self.saved_species_options = {}
        self.individual_drafts.clear()
        self.reviewed_species = []
        self.reviewed_species_counts = []
        self.individual_id = None
        self.references = []
        self.reference = None
        self.frames = []
        self.selected_frame_id = None
        self.review = None
        self.offsets, self.samples = [], []
        self.sample_offset = None
        self.animal_frame_rate = None
        self.sample_drafts.clear()
        self.sample_defaults.clear()
        self.animal_player = self.reference_player = None
        if self.video:
            self.reviewed_species_counts = await run.io_bound(
                self.dp.get_distance_reviewed_species_counts, self.video["video_id"]
            )
            self.reviewed_species = [row["species_id"] for row in self.reviewed_species_counts]
            self.individuals = await run.io_bound(
                self.dp.list_distance_individuals, self.video["video_id"]
            )
            await self.load_saved_species_options()
            self.individual_id = self.individuals[0]["id"] if self.individuals else None
            await self.reload_review(resume=True)
            if self.video["camera_id"]:
                self.references = await run.io_bound(
                    self.dp.get_distance_reference_videos, self.video["video_id"]
                )
            reference_sample = self.reference_sample
            if reference_sample and reference_sample.get("skip_reason"):
                self.selected_frame_id = reference_sample["skip_reason"]
            if reference_sample and reference_sample["reference_video_id"]:
                reference = next(
                    (
                        row
                        for row in self.references
                        if row["id"] == reference_sample["reference_video_id"]
                    ),
                    None,
                )
                if reference:
                    path = reference_path(reference)
                    if path.is_file():
                        probe = await run.io_bound(_probe_video, path)
                        if probe.is_valid and probe.is_web_safe and probe.duration_sec:
                            self.reference = reference
                            self.reference_duration = probe.duration_sec
                            self.frames = await run.io_bound(
                                self.dp.list_reference_frames, reference["id"]
                            )
                            self.selected_frame_id = (
                                reference_sample.get("skip_reason")
                                or reference_sample["reference_frame_id"]
                            )
            if not self.reference and self.references:
                reference = self.references[0]
                path = reference_path(reference)
                if path.is_file():
                    probe = await run.io_bound(_probe_video, path)
                    if probe.is_valid and probe.is_web_safe and probe.duration_sec:
                        self.reference = reference
                        self.reference_duration = probe.duration_sec
                        self.frames = await run.io_bound(
                            self.dp.list_reference_frames, reference["id"]
                        )
            self.ensure_reference_selection()

    def remember_queue(self) -> None:
        set_distance_queue_state(
            self.project_id,
            species_filter=self.species_filter,
            camera_filter=self.camera_filter,
            status_filter=self.status_filter,
            video_id=self.video["video_id"] if self.video else None,
        )

    async def load_queue(self, *, restore: bool = False) -> None:
        saved_video_id = get_distance_queue_state(self.project_id).get("video_id")
        self.queue = await run.io_bound(
            self.dp.get_distance_video_queue,
            self.project_id,
            self.species_filter,
            camera_id=self.camera_filter,
            status=self.status_filter,
        )
        self.index = next(
            (
                index
                for index, row in enumerate(self.queue)
                if restore and row["video_id"] == saved_video_id
            ),
            0,
        )
        self.remember_queue()

    @stable_client
    async def change_queue(self) -> None:
        if self.busy:
            return
        self.busy = True
        try:
            await self.load_queue()
            await self.load_video()
            self.render_workspace.refresh()
            self.render_sidebar.refresh()
        finally:
            self.busy = False
            self.refresh_sample_ui()
            self.render_sample_navigation.refresh()

    @stable_client
    async def move_video(self, delta: int) -> None:
        if self.busy or not 0 <= self.index + delta < len(self.queue):
            return
        self.busy = True
        try:
            self.index += delta
            self.remember_queue()
            await self.load_video()
            self.render_workspace.refresh()
            self.render_sidebar.refresh()
        finally:
            self.busy = False
            self.refresh_sample_ui()
            self.render_sample_navigation.refresh()

    @stable_client
    async def reload_individuals(self) -> None:
        previous = {row["id"]: row for row in self.individuals}
        self.individuals = await run.io_bound(
            self.dp.list_distance_individuals, self.video["video_id"]
        )
        await self.load_saved_species_options()
        for row in self.individuals:
            draft, old = self.individual_drafts.get(row["id"]), previous.get(row["id"])
            if draft is not None and old is not None:
                for key in ("species_id", "age", "sex", "tag_ids"):
                    if draft[key] == old[key]:
                        draft[key] = list(row[key]) if key == "tag_ids" else row[key]
        if self.animal_player:
            configure_anchor(self.animal_player, self.individual, individuals=self.individuals)
        self.render_individuals.refresh()
        await self.reload_review()
        self.render_timing.refresh()
        self.refresh_sample_ui()
        self.render_sample_navigation.refresh()

    async def load_saved_species_options(self) -> None:
        """Fetch labels for saved out-of-vocabulary species without blocking UI rendering."""
        species_ids = list(
            {
                row["species_id"]
                for row in self.individuals
                if row["species_id"] and row["species_id"] not in self.species_options
            }
        )
        if not species_ids:
            self.saved_species_options = {}
            return
        options = await run.io_bound(
            self.dp.get_distance_species_options,
            self.project_id,
            get_language(),
            include_species_ids=species_ids,
        )
        self.saved_species_options = {
            species_id: options[species_id] for species_id in species_ids if species_id in options
        }

    def individual_species_options(self, row: dict) -> dict[str, str]:
        options = dict(self.species_options)
        species_id = row["species_id"]
        if species_id in self.saved_species_options:
            options[species_id] = (
                f"{self.saved_species_options[species_id]} ({t('distance_species_out_of_vocabulary')})"
            )
        return options

    def individual_label(self, row: dict) -> str:
        options = self.individual_species_options(row)
        details = [options.get(row["species_id"], t("species_label"))]
        details.extend(
            t(f"distance_{value.lower()}") for value in (row["age"], row["sex"]) if value
        )
        return f"#{row['individual_number']} · {' · '.join(details)}"

    def default_species(self) -> str | None:
        """Prefer the session target or active individual, then the clip's reviewed species."""
        candidates = [
            self.species_filter,
            self.individual["species_id"] if self.individual else None,
            *self.reviewed_species,
        ]
        return next((value for value in candidates if value in self.species_options), None)

    def render_filters(self) -> None:
        def update(name, event):
            if self.busy:
                event.sender.set_value(getattr(self, name))
                return
            setattr(self, name, event.value)
            return self.change_queue()

        with ui.card().classes("full-width q-mb-md"):
            ui.label(t("filter_section_general")).classes("text-caption q-mt-xs")
            ui.select(
                self.species_options,
                label=t("distance_species_filter"),
                value=self.species_filter,
                clearable=True,
                with_input=True,
                on_change=lambda e: update("species_filter", e),
            ).props("dense outlined").classes("w-full")
            ui.select(
                self.camera_options,
                label=t("camera_filter"),
                value=self.camera_filter,
                clearable=True,
                with_input=True,
                on_change=lambda e: update("camera_filter", e),
            ).props("dense outlined").classes("w-full")
            ui.select(
                {
                    key: t(f"distance_status_{key}")
                    for key in ("not_started", "in_progress", "completed")
                },
                label=t("distance_status_filter"),
                value=self.status_filter,
                clearable=True,
                on_change=lambda e: update("status_filter", e),
            ).props("dense outlined").classes("w-full")

    @ui.refreshable
    def render_sidebar(self) -> None:
        if self.video:
            with ui.card().classes("full-width distance-review-card"):
                with ui.row().classes("items-center w-full q-mb-none no-wrap"):
                    ui.label(t("manual_review")).classes("text-subtitle1 font-weight-medium")
                    ui.space()
                    self.render_timing()
                    ui.icon("info_outline", size="xs").classes("text-grey-6").tooltip(
                        t("distance_intro") + " " + t("distance_preparation_notice")
                    )
                self.render_individuals()
                self.render_reviewed_species()
                self.render_sample_actions()

    @ui.refreshable
    def render_workspace(self) -> None:
        video = self.video
        if not video:
            with ui.card().classes("w-full items-center q-pa-xl"):
                ui.icon("video_library", size="lg").classes("text-grey-5")
                ui.label(t("distance_no_videos"))
            return
        with ui.column().classes("distance-queue"):
            with ui.element("div").classes("distance-queue-header"):
                ui.label(video["camera_id"] or t("unknown")).classes(
                    "text-caption distance-queue-text"
                )
                ui.label(Path(normalize_path_str(video["video_path"])).name).classes(
                    "text-caption text-center distance-queue-text"
                ).tooltip(video["video_path"])
                ui.label(f"{self.index + 1} / {len(self.queue)}").classes("text-caption no-wrap")
            with ui.row().classes("w-full items-center gap-xs q-mt-none no-wrap"):
                with ui.button(on_click=lambda: self.move_video(-1)).props(
                    'flat dense data-distance-action="previous"'
                ) as previous:
                    with ui.row().classes("items-center gap-xs no-wrap"):
                        ui.icon("chevron_left")
                        _shortcut_badge("P")
                previous.set_enabled(self.index > 0)
                slider = (
                    ui.slider(min=0, max=max(len(self.queue) - 1, 1), step=1, value=self.index)
                    .props("dense color=primary")
                    .classes("col")
                )

                @self.client_handler
                async def navigate_queue(event):
                    target = int(event.args)
                    if target != self.index:
                        await self.move_video(target - self.index)
                        if self.index != target:
                            slider.set_value(self.index)

                slider.on("change", navigate_queue)
                with ui.button(on_click=lambda: self.move_video(1)).props(
                    'flat dense data-distance-action="next"'
                ) as next_button:
                    with ui.row().classes("items-center gap-xs no-wrap"):
                        _shortcut_badge("N")
                        ui.icon("chevron_right")
                next_button.set_enabled(self.index + 1 < len(self.queue))
        with ui.element("div").classes("distance-grid"):
            self.render_animal()
            self.render_references()

    @ui.refreshable
    def render_animal(self) -> None:
        video = self.video
        with ui.card().classes("distance-panel distance-player") as panel:
            ui.label(t("distance_animal_video")).classes("text-subtitle1 ellipsis w-full").tooltip(
                t("distance_animal_video")
            )
            path = Path(normalize_path_str(video["video_path"]))
            cache = inspect_transcoded_cache(video.get("transcoded_path"), validate_contents=False)
            if cache.current:
                path = Path(video["transcoded_path"])
            if not path.is_file():
                ui.label(t("video_not_found", path=str(path))).classes("text-negative break-all")
                return
            if video.get("is_web_safe") is False and not cache.current:
                ui.label(t("distance_needs_conversion")).classes("text-grey-6")

                @self.client_handler
                async def convert():
                    conversion.set_enabled(False)
                    try:
                        result = await run.io_bound(
                            self.dp.ensure_web_safe_video, video["video_id"]
                        )
                        if result.success:
                            video["transcoded_path"] = result.new_path
                            if self.review:
                                await self.load_sample_timing()
                            self.render_animal.refresh()
                            self.render_individuals.refresh()
                        else:
                            ui.notify(t("transcode_failed", error=result.error), type="negative")
                    finally:
                        conversion.set_enabled(True)

                conversion = ui.button(t("transcode_for_playback"), on_click=convert, icon="movie")
                return
            self.animal_player = render_custom_video_player(
                self.media_url(path),
                video.get("duration_sec") or 0,
                str(uuid.uuid4()),
                autoplay=False,
                keyboard=False,
                compact=True,
            )
            self.animal_player.on(
                "error", lambda: ui.notify(t("distance_media_error"), type="warning")
            )
            self.animal_player.on("loadedmetadata", self.seek_sample)
            install_manual_pause_snap(self.animal_player, self.on_manual_pause)
            playback_shortcuts(panel, self.animal_player)
            install_anchor_editor(
                self.animal_player,
                self.save_anchor,
                self.individual,
                individuals=self.individuals,
                frame_rate=self.animal_frame_rate,
            )
            if self.sample_offset is not None:
                self.seek_sample()
            self.render_sample_navigation()
            self.render_tracks()

    @ui.refreshable
    def render_tracks(self) -> None:
        if not self.video:
            return
        with ui.column().classes("w-full gap-1 distance-tracks"):
            ui.label(t("distance_track_title")).classes("text-caption text-weight-medium")
            ui.label(t("distance_track_help")).classes("text-caption text-grey-6")
            chart_height = 295 + max(28, len(self.individuals) * 32)
            options = track_options(
                self.individuals,
                self.samples,
                self.offsets,
                self.video.get("duration_sec") or 0,
                self.individual_id,
                self.sample_offset,
                chart_height,
            )
            ui.echart(options, renderer="svg").classes("w-full").style(f"height:{chart_height}px")

    @ui.refreshable
    def render_reviewed_species(self) -> None:
        if not self.reviewed_species_counts:
            return
        ui.label(t("distance_review_species_title")).classes("text-caption text-weight-medium")
        for row in self.reviewed_species_counts:
            name = self.species_options.get(row["species_id"], row["species_id"])
            ui.label(t("distance_review_species_count", species=name, count=row["count"])).classes(
                "text-caption text-grey-7"
            )

    def completion_discrepancies(self) -> list[dict]:
        distance_counts: dict[str, int] = {}
        for individual in self.individuals:
            species_id = individual.get("species_id")
            if species_id:
                distance_counts[species_id] = distance_counts.get(species_id, 0) + 1
        return [
            {**row, "distance_count": distance_counts.get(row["species_id"], 0)}
            for row in self.reviewed_species_counts
            if distance_counts.get(row["species_id"], 0) == 0
            or row["count"] > distance_counts[row["species_id"]]
        ]

    def _individual_views_context(self) -> IndividualViewsContext:
        return IndividualViewsContext(
            review_active=bool(self.review),
            camera_id=self.video["camera_id"],
            has_player=bool(self.animal_player),
            busy=self.busy,
            busy_state=self,
            species_options=self.species_options,
            individuals=self.individuals,
            individual_id=self.individual_id,
            drafts=self.individual_drafts,
            tag_options=self.tag_options,
            all_tag_options=self.all_tag_options,
            samples=self.samples,
            species_choices=self.individual_species_options,
            label=self.individual_label,
            on_clock=self.clock_dialog,
            on_add=self.add_individual_dialog,
            on_select=self.select_individual,
            on_attribute=self.save_individual_attribute,
            on_delete=self.delete_individual,
            on_redraw=self.redraw_anchor_dialog,
            on_first_seen=self.go_to_first_seen,
            render_progress=self.render_individual_progress,
        )

    @ui.refreshable
    def render_individuals(self) -> None:
        render_individuals_view(self._individual_views_context())

    def render_individual_card(self, row: dict) -> None:
        render_individual_card_view(self._individual_views_context(), row)

    @stable_client
    async def save_individual_attribute(self, individual_id: str, field: str, value) -> None:
        # Serialize rapid changes and merge each field with the latest saved values.
        async with self.individual_attributes_lock:
            row = next((row for row in self.individuals if row["id"] == individual_id), None)
            if row is None or row[field] == value:
                return
            if self.busy:
                self.render_individuals.refresh()
                return
            attributes = {key: row[key] for key in ("species_id", "age", "sex", "tag_ids")}
            attributes[field] = value
            self.busy = True
            try:
                await run.io_bound(
                    self.dp.set_distance_individual_attributes,
                    individual_id,
                    **attributes,
                    labeled_by=get_annotator_name(),
                )
                self.individual_drafts.pop(individual_id, None)
                await self.reload_individuals()
            except ValueError as exc:
                self.individual_drafts.pop(individual_id, None)
                self.render_individuals.refresh()
                ui.notify(str(exc), type="negative")
            finally:
                self.busy = False
                self.refresh_sample_ui()
                self.render_sample_navigation.refresh()

    @stable_client
    async def delete_individual(self, individual_id: str) -> None:
        if self.busy or not any(row["id"] == individual_id for row in self.individuals):
            return
        self.busy = True
        try:
            position = next(
                i for i, row in enumerate(self.individuals) if row["id"] == individual_id
            )
            await run.io_bound(self.dp.delete_distance_individual, individual_id)
            self.individual_drafts.pop(individual_id, None)
            self.sample_defaults.pop(individual_id, None)
            # Deletion can also invalidate samples linked to a carrier or infant.
            self.sample_drafts.clear()
            await self.reload_individuals()
            if self.individual_id == individual_id:
                if self.individuals:
                    next_id = self.individuals[min(position, len(self.individuals) - 1)]["id"]
                    await self.activate_individual(next_id)
                else:
                    self.individual_id = self.sample_offset = self.selected_frame_id = None
                    if self.animal_player:
                        configure_anchor(self.animal_player, None)
                    self.render_individuals.refresh()
            else:
                await self.restore_sample()
        except ValueError as exc:
            ui.notify(str(exc), type="negative")
        finally:
            self.busy = False
            self.refresh_sample_ui()
            self.render_sample_navigation.refresh()

    @stable_client
    async def go_to_first_seen(self, individual_id: str) -> None:
        if (
            self.busy
            or not self.animal_player
            or not any(row["id"] == individual_id for row in self.individuals)
        ):
            return
        self.busy = True
        try:
            await self.activate_individual(individual_id)
        finally:
            self.busy = False
            self.refresh_sample_ui()
            self.render_sample_navigation.refresh()

    @stable_client
    async def select_individual(self, individual_id: str) -> None:
        if (
            self.busy
            or individual_id == self.individual_id
            or not any(row["id"] == individual_id for row in self.individuals)
        ):
            return
        self.busy = True
        try:
            await self.activate_individual(individual_id)
        finally:
            self.busy = False
            self.refresh_sample_ui()
            self.render_sample_navigation.refresh()

    async def activate_individual(
        self, individual_id: str, *, first_seen_offset_ms: int | None = None
    ) -> None:
        """Inspect first seen; creation keeps its already displayed identifying frame."""
        self.remember_sample_choices()
        await run.io_bound(
            self.dp.set_distance_active_individual,
            self.video["video_id"],
            get_annotator_name(),
            individual_id,
        )
        self.individual_id = individual_id
        await self.reload_review()
        identifying_offset = (
            self.individual["first_seen_offset_ms"]
            if first_seen_offset_ms is None
            else first_seen_offset_ms
        )
        # Inspection can be off-grid; retain the creation workflow's next-sample
        # selection so returning to annotation targets a scheduled stop.
        self.sample_offset = next(
            (offset for offset in self.offsets if offset >= identifying_offset),
            self.offsets[-1] if self.offsets else None,
        )
        if self.animal_player:
            configure_anchor(self.animal_player, self.individual, individuals=self.individuals)
        await self.restore_sample(seek=False)
        if self.animal_player and first_seen_offset_ms is None:
            seek_paused(self.animal_player, identifying_offset)
        self.render_individuals.refresh()
        self.refresh_sample_ui()
        self.render_sample_navigation.refresh()

    @stable_client
    async def save_anchor(self, event) -> None:
        if self.busy:
            return
        payload = event.args
        if not any(row["id"] == payload["individual_id"] for row in self.individuals):
            return
        self.busy = True
        try:
            await run.io_bound(
                self.dp.set_distance_individual_anchor,
                payload["individual_id"],
                payload["offset_ms"],
                anchor=payload["anchor"],
                labeled_by=get_annotator_name(),
            )
            await self.reload_individuals()
            ui.notify(t("distance_marker_saved"), type="positive")
        except ValueError as exc:
            ui.notify(str(exc), type="negative")
        finally:
            self.busy = False
            self.refresh_sample_ui()
            self.render_sample_navigation.refresh()

    @stable_client
    async def create_individual(
        self, video_id: str, species_id: str, offset_ms: int, anchor: dict[str, float]
    ) -> bool:
        if self.busy or not self.video or self.video["video_id"] != video_id:
            return False
        self.busy = True
        try:
            row = await run.io_bound(
                self.dp.create_distance_individual,
                video_id,
                species_id,
                offset_ms,
                anchor=anchor,
                labeled_by=get_annotator_name(),
            )
            await self.reload_individuals()
            await self.activate_individual(row["id"], first_seen_offset_ms=offset_ms)
            return True
        finally:
            self.busy = False
            self.refresh_sample_ui()
            self.render_sample_navigation.refresh()

    def _anchor_dialog_is_current(self, dialog: ui.dialog, video_id: str) -> bool:
        return bool(
            dialog.value and not self.busy and self.video and self.video["video_id"] == video_id
        )

    @stable_client
    async def redraw_anchor_dialog(self, individual_id: str) -> None:
        row = next((item for item in self.individuals if item["id"] == individual_id), None)
        if self.busy or not self.video or not self.animal_player or row is None:
            return
        video_id = self.video["video_id"]
        offset_ms = row["first_seen_offset_ms"]
        filters = await image_filters(self.animal_player)

        async def save_anchor(anchor):
            self.busy = True
            try:
                await run.io_bound(
                    self.dp.set_distance_individual_anchor,
                    individual_id,
                    offset_ms,
                    anchor=anchor,
                    labeled_by=get_annotator_name(),
                )
                await self.reload_individuals()
                return True
            finally:
                self.busy = False
                self.refresh_sample_ui()
                self.render_sample_navigation.refresh()

        await open_redraw_anchor_dialog(
            AnchorDialogContext(
                row=row,
                player_source=self.animal_player.source,
                duration_sec=self.video.get("duration_sec") or 0,
                frame_rate=self.animal_frame_rate,
                filters=filters,
                species_options=self.species_options,
                default_species=None,
                guard=self.client_handler,
                is_current=lambda dialog: bool(
                    self._anchor_dialog_is_current(dialog, video_id)
                    and any(
                        item["id"] == individual_id and item["first_seen_offset_ms"] == offset_ms
                        for item in self.individuals
                    )
                ),
                create_individual=lambda _species, _offset, _anchor: False,
                save_anchor=save_anchor,
            )
        )

    @stable_client
    async def add_individual_dialog(self) -> None:
        if self.busy or not self.video or not self.animal_player or not self.review:
            return
        video_id = self.video["video_id"]
        player = self.animal_player
        try:
            offset_ms = await capture_offset(player)
        except (ValueError, TimeoutError):
            ui.notify(t("distance_player_not_ready"), type="warning")
            return
        if not self.video or self.video["video_id"] != video_id:
            return
        draft = {"id": str(uuid.uuid4()), "first_seen_offset_ms": offset_ms}
        filters = await image_filters(player)
        await open_create_individual_dialog(
            AnchorDialogContext(
                row=draft,
                player_source=player.source,
                duration_sec=self.video.get("duration_sec") or 0,
                frame_rate=self.animal_frame_rate,
                filters=filters,
                species_options=self.species_options,
                default_species=self.default_species(),
                guard=self.client_handler,
                is_current=lambda dialog: self._anchor_dialog_is_current(dialog, video_id),
                create_individual=lambda species_id, first_seen, anchor: self.create_individual(
                    video_id, species_id, first_seen, anchor
                ),
                save_anchor=lambda _anchor: False,
            )
        )

    @ui.refreshable
    def render_timing(self) -> None:
        if not self.review:
            return
        ui.button(
            icon="verified",
            on_click=self.clock_dialog,
        ).props("flat round dense").tooltip(self.clock_description())

    def clock_description(self) -> str:
        anchor = self.review.get("clock_anchor_offset_ms")
        if anchor is not None:
            return t(
                "distance_clock_aligned",
                time=sample_camera_time(self.review, anchor).isoformat(sep=" "),
                offset=frame_time(self.review["clock_anchor_playback_ms"]),
                annotator=self.review["verified_by"],
            )
        return t(
            "distance_verified_time",
            time=self.review["recorded_start"].isoformat(sep=" "),
            annotator=self.review["verified_by"],
        )

    @stable_client
    async def clock_dialog(self) -> None:
        if self.busy or not self.video:
            return
        video = self.video
        video_id = video["video_id"]
        path = Path(normalize_path_str(video["video_path"]))
        cache = inspect_transcoded_cache(video.get("transcoded_path"), validate_contents=False)
        if cache.current:
            path = Path(video["transcoded_path"])
        playable = path.is_file() and (cache.current or video.get("is_web_safe") is not False)
        if playable:
            await self.load_sample_timing()
        if not self.video or self.video["video_id"] != video_id:
            return
        review = self.review
        proposed = review["recorded_start"] if review else video.get("created_at")
        if isinstance(proposed, str):
            proposed = datetime.fromisoformat(proposed)
        if proposed and review and review.get("clock_anchor_offset_ms") is not None:
            proposed += timedelta(milliseconds=review["clock_anchor_offset_ms"])
        elif proposed:
            proposed = proposed.replace(microsecond=0) + timedelta(seconds=2 - proposed.second % 2)

        async def on_calibrated():
            await self.reload_review()
            self.resume_sample()
            await self.restore_sample()
            self.render_timing.refresh()
            self.render_individuals.refresh()
            self.render_sample_navigation.refresh()
            self.refresh_sample_ui()

        unavailable = (
            t("video_not_found", path=str(path))
            if not path.is_file()
            else t("distance_needs_conversion")
        )
        await open_clock_alignment_dialog(
            ClockDialogContext(
                client=self.client,
                provider=self.dp,
                video_id=video_id,
                source=(self.animal_player.source if self.animal_player else self.media_url(path))
                if playable
                else None,
                duration_sec=video.get("duration_sec") or 0,
                frame_rate=self.animal_frame_rate or 25,
                initial_offset_ms=(
                    review.get("clock_anchor_playback_ms") or 0 if review and self.samples else 0
                ),
                clock_description=self.clock_description() if review else None,
                proposed_camera_time=proposed,
                opening_even_second=bool(review and review.get("opening_even_second")),
                locked=bool(review and self.samples),
                unavailable_message=unavailable,
                show_estimated_steps=self.animal_frame_rate is None,
                guard=self.client_handler,
                is_current=lambda: bool(self.video and self.video["video_id"] == video_id),
                is_busy=lambda: self.busy,
                set_busy=lambda value: setattr(self, "busy", value),
                annotator=get_annotator_name(),
                on_calibrated=on_calibrated,
                on_refresh=self.refresh_sample_ui,
            )
        )

    @ui.refreshable
    def render_individual_progress(self) -> None:
        if not self.review or not self.individual or not self.offsets:
            return
        ui.label(
            t(
                "distance_sample_progress",
                reviewed=len(self.reviewed_offsets().intersection(self.offsets)),
                total=len(self.offsets),
            )
        ).classes("text-caption text-grey-7 distance-individual-progress")

    @ui.refreshable
    def render_sample_navigation(self) -> None:
        """Show sample timing and replay directly below the animal player."""
        if not self.review or not self.individual or self.sample_offset is None:
            return
        with ui.row().classes("distance-animal-timing items-center w-full gap-xs"):
            recorded_time = sample_camera_time(self.review, self.sample_offset)
            ui.label(t("distance_sample_time", time=recorded_time.isoformat(sep=" "))).classes(
                "text-caption text-weight-medium"
            )
            if self.offsets.index(self.sample_offset) > 0 and self.animal_player:
                ui.button(
                    t("distance_play_interval"), icon="replay", on_click=self.replay_interval
                ).props("flat dense").set_enabled(not self.busy)

    @stable_client
    async def replay_interval(self) -> None:
        if self.busy or self.sample_offset is None:
            return
        position = self.offsets.index(self.sample_offset)
        if not position:
            return
        self.busy = True
        self.refresh_sample_ui()
        self.render_sample_navigation.refresh()
        try:
            with self.client:
                await play_to_sample(
                    self.animal_player,
                    self.sample_seek_offset(self.offsets[position - 1]),
                    self.sample_seek_offset(self.sample_offset),
                )
        except (ValueError, TimeoutError):
            ui.notify(t("distance_playback_interrupted"), type="warning")
        finally:
            self.busy = False
            self.refresh_sample_ui()
            self.render_sample_navigation.refresh()

    def sample_status_label(self, offset: int) -> str:
        sample = next(
            (
                row
                for row in self.samples
                if row["individual_id"] == self.individual_id and row["offset_ms"] == offset
            ),
            None,
        )
        key = (
            "distance_unreviewed"
            if not sample
            else ("distance_skipped" if sample["status"] == "skipped" else "distance_reviewed")
        )
        return t(key)

    def _sample_controls_context(self, *, include_draft: bool) -> SampleControlsContext:
        sample = self.sample
        carrier = next(
            (
                row
                for row in self.individuals
                if sample and row["id"] == sample["carried_by_individual_id"]
            ),
            None,
        )
        draft = (
            self.current_sample_draft()
            if include_draft and self.individual and self.sample_offset is not None
            else None
        )
        return SampleControlsContext(
            review_active=bool(self.review),
            individual=self.individual,
            individual_id=self.individual_id,
            sample_offset=self.sample_offset,
            sample=sample,
            carrier=carrier,
            draft=draft,
            behavior_options=self.behavior_options,
            individuals=self.individuals,
            individual_label=self.individual_label,
            frames=self.frames,
            selection=self._selection,
            skip_choices=SKIP_CHOICES,
            animal_player=self.animal_player,
            busy=self.busy,
            video_completed=bool(self.review and self.review["completed_at"]),
            remaining_offsets=self.remaining_sample_offsets(),
            all_individuals_completed=bool(
                self.individuals
                and all(row["review_status"] == "completed" for row in self.individuals)
            ),
            on_confirm=self.commit_sample,
            on_apply_to_end=self.apply_to_end_dialog,
            on_finish_video=self.finish_video,
        )

    @ui.refreshable
    def render_samples(self) -> None:
        render_sample_controls(self._sample_controls_context(include_draft=True))

    @ui.refreshable
    def render_sample_actions(self) -> None:
        render_sample_actions_view(self._sample_controls_context(include_draft=False))

    def remaining_sample_offsets(self) -> list[int]:
        if not self.individual or self.sample_offset not in self.offsets:
            return []
        return [offset for offset in self.offsets if offset >= self.sample_offset]

    def apply_to_end_assignment(self) -> dict | None:
        """Snapshot the unsaved choice, including sample metadata for visibility choices."""
        reason = self.selected_skip_reason
        if reason is None and not any(
            frame["id"] == self.selected_frame_id for frame in self.frames
        ):
            return None
        draft = self.current_sample_draft()
        return {
            "reference_frame_id": None if reason else self.selected_frame_id,
            "skip_reason": reason,
            "behavior_ids": list(draft["behavior_ids"]),
            "infant_id": draft["infant_id"]
            if draft["carries"] and draft["infant_id"] != "_new"
            else None,
            "create_infant": bool(draft["carries"] and draft["infant_id"] == "_new"),
        }

    @stable_client
    async def apply_to_end_dialog(self) -> None:
        if self.busy or not self.remaining_sample_offsets():
            return
        assignment = self.apply_to_end_assignment()
        if assignment is None:
            return
        video_id = self.video["video_id"]
        individual_id = self.individual_id
        offset_ms = self.sample_offset
        offsets = self.remaining_sample_offsets()
        frame = next((row for row in self.frames if row["id"] == self.selected_frame_id), None)
        choice_label = (
            f"{frame['distance_m']:g} m"
            if frame
            else t(
                "distance_absent"
                if assignment["skip_reason"] == "individual absent"
                else "distance_midpoint_hidden"
            )
        )

        def is_current():
            return bool(
                not self.busy
                and self.video
                and self.video["video_id"] == video_id
                and self.individual_id == individual_id
                and self.sample_offset == offset_ms
                and self.apply_to_end_assignment() == assignment
            )

        async def apply():
            if not is_current():
                return False
            self.busy = True
            try:
                affected_keys = {(individual_id, offset) for offset in offsets} | {
                    (row["individual_id"], row["offset_ms"])
                    for row in self.samples
                    if row["carried_by_individual_id"] == individual_id
                    and row["offset_ms"] in offsets
                }
                saved = await run.io_bound(
                    self.dp.apply_distance_to_end,
                    individual_id,
                    offset_ms,
                    **assignment,
                    labeled_by=get_annotator_name(),
                )
                if saved["infant_id"]:
                    affected_keys.update((saved["infant_id"], offset) for offset in offsets)
                for key in affected_keys:
                    self.sample_drafts.pop(key, None)
                for affected_id in {key[0] for key in affected_keys}:
                    self.sample_defaults.pop(affected_id, None)
                await self.reload_individuals()
                self.sample_offset = max(offsets)
                await self.restore_sample()
                ui.notify(
                    t("distance_apply_to_end_saved", count=len(saved["samples"])),
                    type="positive",
                )
                return True
            except (ValueError, IntegrityError) as exc:
                ui.notify(
                    str(exc) if isinstance(exc, ValueError) else t("distance_duplicate"),
                    type="warning",
                )
                return False
            finally:
                self.busy = False
                self.refresh_sample_ui()
                self.render_sample_navigation.refresh()

        await open_apply_to_end_dialog(
            ApplyToEndDialogContext(
                assignment=assignment,
                video_id=video_id,
                individual_id=individual_id,
                sample_offset=offset_ms,
                remaining_offsets=offsets,
                individual_number=self.individual["individual_number"],
                choice_label=choice_label,
                guard=self.client_handler,
                is_current=is_current,
                apply=apply,
            )
        )

    @stable_client
    async def commit_sample(self, reason: str | None = None) -> None:
        if self.busy or not self.individual or self.sample_offset is None:
            return
        reason = reason or self.selected_skip_reason
        if reason is None and not any(
            frame["id"] == self.selected_frame_id for frame in self.frames
        ):
            return
        individual_id, offset_ms = self.individual_id, self.sample_offset
        draft = self.sample_drafts[(individual_id, offset_ms)]
        reference_frame_id = self.selected_frame_id
        self.busy = True
        self.refresh_sample_ui()
        try:
            frame_offset = self.sample_frame_offset(offset_ms)
            actual_offset = await capture_offset(self.animal_player, exact=True)
            tolerance = frame_tolerance_ms(self.animal_frame_rate) if offset_ms else 0.001
            if not frame_offset <= actual_offset <= self.sample_seek_offset(offset_ms) + tolerance:
                self.seek_sample()
                ui.notify(t("distance_review_restored_sample"), type="warning")
                return
            saved = await run.io_bound(
                self.dp.save_distance_sample,
                individual_id,
                offset_ms,
                reference_frame_id=reference_frame_id if reason is None else None,
                skip_reason=reason,
                behavior_ids=list(draft["behavior_ids"]),
                infant_id=draft["infant_id"]
                if draft["carries"] and draft["infant_id"] != "_new"
                else None,
                create_infant=bool(draft["carries"] and draft["infant_id"] == "_new"),
                creation_offset_ms=actual_offset,
                labeled_by=get_annotator_name(),
            )
            self.selected_frame_id = reason or reference_frame_id
            self.remember_sample_choices()
            if isinstance(saved, dict) and saved.get("infant_id"):
                self.sample_defaults[individual_id]["infant_id"] = saved["infant_id"]
            self.sample_drafts.pop((individual_id, offset_ms), None)
            # The originating confirm button is deleted by refresh. Async player
            # commands must continue in the page's stable client slot.
            with self.client:
                await self.reload_individuals()
                position = self.offsets.index(offset_ms)
                next_offset = self.offsets[min(position + 1, len(self.offsets) - 1)]
                self.render_sample_navigation.refresh()
                # Every scheduled stop is visited, including previously reviewed
                # samples. Confirming the final stop keeps it selected.
                if next_offset > offset_ms:
                    anchor_mode(self.animal_player, None)
                    await play_to_sample(
                        self.animal_player,
                        self.sample_seek_offset(offset_ms),
                        self.sample_seek_offset(next_offset),
                    )
                else:
                    self.seek_sample()
                # Delayed UI refreshes must still refer to the frame being left
                # during playback, so they cannot create a draft for the next
                # frame using the previous frame's reference selection.
                self.sample_offset = next_offset
                # Rebuilding a reference player adds global event listeners and
                # may remount other videos. Switch evidence only after playback
                # has finished and the animal is paused at the next stop.
                await self.restore_sample(seek=False)
                self.refresh_sample_ui()
            ui.notify(
                t(
                    "distance_end_of_video"
                    if next_offset == offset_ms
                    else "distance_sample_saved"
                ),
                type="positive",
            )
        except (ValueError, TimeoutError) as exc:
            ui.notify(
                t("distance_player_not_ready")
                if isinstance(exc, TimeoutError) or str(exc) == "distance_player_not_ready"
                else t("distance_playback_interrupted")
                if str(exc) == "distance_playback_interrupted"
                else str(exc),
                type="warning",
            )
        finally:
            self.busy = False
            self.refresh_sample_ui()
            self.render_sample_navigation.refresh()

    async def move_sample(self, delta: int) -> None:
        if self.busy or self.sample_offset not in self.offsets:
            return
        position = self.offsets.index(self.sample_offset) + delta
        if 0 <= position < len(self.offsets):
            await self.choose_sample(self.offsets[position])

    @stable_client
    async def choose_sample(self, offset_ms: int) -> None:
        if self.busy or offset_ms not in self.offsets:
            return
        self.busy = True
        try:
            self.remember_sample_choices()
            self.sample_offset = offset_ms
            await self.restore_sample()
        finally:
            self.busy = False
            self.refresh_sample_ui()
            self.render_sample_navigation.refresh()

    @stable_client
    async def restore_sample(self, *, seek: bool = True) -> None:
        if seek:
            self.seek_sample()
        sample = self.sample
        # Restore this frame's saved evidence or draft. New frames carry forward
        # the current choice; resuming a session falls back to preceding evidence.
        draft = self.sample_drafts.get((self.individual_id, self.sample_offset))
        reference_sample = (
            draft
            if draft and (draft.get("reference_frame_id") or draft.get("skip_reason"))
            else (
                sample
                if sample and (sample.get("reference_frame_id") or sample.get("skip_reason"))
                else (self.reference_sample if self.selected_frame_id is None else None)
            )
        )
        if reference_sample and reference_sample["reference_video_id"]:
            if (
                not self.reference
                or self.reference["id"] != reference_sample["reference_video_id"]
            ):
                reference = next(
                    (
                        row
                        for row in self.references
                        if row["id"] == reference_sample["reference_video_id"]
                    ),
                    None,
                )
                if reference:
                    await self.open_reference(
                        reference,
                        restoring=True,
                        selected_frame_id=reference_sample.get("skip_reason")
                        or reference_sample["reference_frame_id"],
                    )
        if reference_sample:
            self.selected_frame_id = (
                reference_sample.get("skip_reason") or reference_sample["reference_frame_id"]
            )
        self.ensure_reference_selection()
        self.seek_reference()
        self.render_frames.refresh()

    async def on_manual_pause(self, event) -> None:
        if self.busy or not self.individual or not self.offsets:
            return
        offset_ms = event.args.get("offset_ms") if isinstance(event.args, dict) else None
        if not isinstance(offset_ms, (int, float)):
            return
        target = next((offset for offset in reversed(self.offsets) if offset <= offset_ms), None)
        if target is not None:
            if target != self.sample_offset:
                await self.choose_sample(target)
            seek_paused(self.animal_player, self.sample_seek_offset(target))

    def seek_sample(self) -> None:
        if self.animal_player and self.sample_offset is not None:
            anchor_mode(self.animal_player, None)
            try:
                seek_paused(self.animal_player, self.sample_seek_offset(self.sample_offset))
            except ValueError:
                ui.notify(t("distance_player_not_ready"), type="warning")

    async def complete_video(self, dialog=None) -> bool:
        if self.busy or not self.video:
            return False
        previous_queue = list(self.queue)
        previous_index = self.index
        completed_video = self.video
        self.busy = True
        try:
            await run.io_bound(
                self.dp.finish_distance_video, completed_video["video_id"], get_annotator_name()
            )
            fresh_queue = await run.io_bound(
                self.dp.get_distance_video_queue,
                self.project_id,
                self.species_filter,
                camera_id=self.camera_filter,
                status=self.status_filter,
            )
            next_video_id = (
                previous_queue[previous_index + 1]["video_id"]
                if previous_index + 1 < len(previous_queue)
                else None
            )
            fresh_ids = [row["video_id"] for row in fresh_queue]
            if next_video_id not in fresh_ids:
                if completed_video["video_id"] in fresh_ids:
                    fresh_index = fresh_ids.index(completed_video["video_id"])
                    next_video_id = (
                        fresh_ids[fresh_index + 1] if fresh_index + 1 < len(fresh_ids) else None
                    )
                elif previous_index < len(fresh_queue):
                    next_video_id = fresh_queue[previous_index]["video_id"]
                else:
                    next_video_id = None
            if next_video_id:
                self.queue = fresh_queue
                self.index = fresh_ids.index(next_video_id)
            else:
                self.queue = fresh_queue
                if completed_video["video_id"] in fresh_ids:
                    self.index = fresh_ids.index(completed_video["video_id"])
                else:
                    self.queue.insert(min(previous_index, len(self.queue)), completed_video)
                    self.index = min(previous_index, len(self.queue) - 1)
            self.remember_queue()
            await self.load_video()
            self.render_workspace.refresh()
            self.render_sidebar.refresh()
            if dialog:
                dialog.close()
            if not next_video_id:
                ui.notify(t("distance_queue_ended"), type="info")
            return True
        except ValueError as exc:
            ui.notify(str(exc), type="warning")
            return False
        finally:
            self.busy = False
            self.refresh_sample_ui()
            self.render_sample_navigation.refresh()

    @stable_client
    async def finish_video(self) -> None:
        if self.busy:
            return
        discrepancies = self.completion_discrepancies()
        if not discrepancies:
            await self.complete_video()
            return
        with ui.dialog() as dialog, ui.card().classes("w-full").style("max-width:520px"):
            ui.label(t("distance_possible_omissions_title")).classes("text-h6")
            ui.label(t("distance_possible_omissions_help"))
            for row in discrepancies:
                name = self.species_options.get(row["species_id"], row["species_id"])
                ui.label(
                    t(
                        "distance_possible_omission",
                        species=name,
                        reviewed=row["count"],
                        distance=row["distance_count"],
                    )
                )

            @self.client_handler
            async def ignore_and_finish():
                await self.complete_video(dialog)

            with ui.row().classes("w-full justify-end"):
                ui.button(t("distance_return_to_annotation"), on_click=dialog.close).props("flat")
                ui.button(t("distance_ignore_and_finish"), on_click=ignore_and_finish).props(
                    "color=warning"
                )
        dialog.open()

    def _reference_views_context(self) -> ReferenceViewsContext:
        return ReferenceViewsContext(
            has_camera=bool(self.video and self.video.get("camera_id")),
            references=self.references,
            reference=self.reference,
            reference_duration=self.reference_duration,
            frames=self.frames,
            selection_id=self.selected_frame_id,
            skip_choices=SKIP_CHOICES,
            busy=self.busy,
            busy_state=self,
            reference_player=self.reference_player,
            render_player=self._render_reference_player,
            reference_name=lambda row: Path(normalize_path_str(row["video_path"])).name,
            prepare_reference=self.prepare_reference_dialog,
            refresh_references=self.refresh_references,
            seek_reference=self.seek_reference,
            reference_load_failed=self.reference_load_failed,
            cycle_reference=self.cycle_reference,
            open_reference=self.open_reference,
            preview_frame=self.preview_frame,
            move_distance=self.move_distance,
            reference_thumbnail_path=self.reference_thumbnail_path,
            load_reference_thumbnail=self.load_reference_thumbnail,
            render_controls=self.render_reference_controls,
            render_frames=self.render_frames,
            render_samples=self.render_samples,
        )

    def _render_reference_player(self, reference: dict, duration: float, panel):
        self.reference_player = render_custom_video_player(
            self.media_url(reference_path(reference)),
            duration,
            str(uuid.uuid4()),
            autoplay=False,
            keyboard=False,
            compact=True,
        )
        self.reference_player.on(
            "loadedmetadata", lambda: self.seek_reference() if not self.busy else None
        )
        self.reference_player.on("error", self.reference_load_failed)
        selected = next(
            (frame for frame in self.frames if frame["id"] == self.selected_frame_id), None
        )
        if selected:
            seek_paused(self.reference_player, reference_frame_offset(selected))
        playback_shortcuts(panel, self.reference_player)
        return self.reference_player

    @ui.refreshable
    def render_references(self) -> None:
        render_references_view(self._reference_views_context())

    @ui.refreshable
    def render_reference_controls(self) -> None:
        render_reference_controls_view(self._reference_views_context())

    def reference_thumbnail_path(self, reference: dict) -> Path | None:
        path = reference_path(reference)
        if not path.is_file():
            return None
        stat = path.stat()
        key = hashlib.sha256(
            f"{path.resolve()}:{stat.st_mtime_ns}:{stat.st_size}".encode()
        ).hexdigest()
        return get_user_data_dir() / "thumbnails" / f"distance-{key}.jpg"

    async def load_reference_thumbnail(self, reference: dict, image) -> None:
        path = reference_path(reference)
        thumbnail = self.reference_thumbnail_path(reference)
        if thumbnail is None:
            return
        if not thumbnail.is_file():
            if thumbnail not in self.thumbnail_tasks:
                self.thumbnail_tasks[thumbnail] = asyncio.create_task(
                    run.io_bound(generate_thumbnail, path, thumbnail)
                )
            await self.thumbnail_tasks[thumbnail]
        if thumbnail.is_file() and not image.is_deleted:
            image.set_source(str(thumbnail))

    async def cycle_reference(self) -> None:
        if self.busy or len(self.references) < 2:
            return
        position = next(
            (
                i
                for i, row in enumerate(self.references)
                if self.reference and row["id"] == self.reference["id"]
            ),
            -1,
        )
        await self.open_reference(self.references[(position + 1) % len(self.references)])

    def distance_choices(self) -> list[dict]:
        choices = {frame["distance_m"]: frame for frame in reversed(self.frames)}
        selected = next(
            (frame for frame in self.frames if frame["id"] == self.selected_frame_id), None
        )
        if selected:
            choices[selected["distance_m"]] = selected
        return [choices[meters] for meters in sorted(choices)] + list(SKIP_CHOICES)

    def move_distance(self, delta: int) -> None:
        if self.busy:
            return
        choices = self.distance_choices()
        if not choices:
            return
        position = next(
            (i for i, frame in enumerate(choices) if frame["id"] == self.selected_frame_id), None
        )
        target = 0 if position is None else max(0, min(len(choices) - 1, position + delta))
        if choices[target]["id"] != self.selected_frame_id:
            self.preview_frame(choices[target])

    @ui.refreshable
    def render_frames(self) -> None:
        render_reference_frames(self._reference_views_context())

    @stable_client
    async def prepare_reference_dialog(self) -> None:
        if self.busy or not self.reference or not self.reference_player:
            return
        reference = self.reference
        reference_id = reference["id"]
        frame_rate, prepared_frames = await asyncio.gather(
            run.io_bound(_probe_frame_rate, reference_path(reference)),
            run.io_bound(self.dp.list_reference_frames, reference_id),
        )
        if not self.reference or self.reference["id"] != reference_id:
            return
        from review_app.app.pages.distance_review.reference_preparation import (
            ReferencePreparationContext,
            open_reference_preparation,
        )

        await open_reference_preparation(
            ReferencePreparationContext(
                client=self.client,
                provider=self.dp,
                reference=reference,
                source=self.reference_player.source,
                duration=self.reference_duration,
                frame_rate=frame_rate,
                prepared_frames=prepared_frames,
                guard=self.client_handler,
                is_current=lambda candidate_id: bool(
                    not self.busy and self.reference and self.reference["id"] == candidate_id
                ),
                on_frames_saved=lambda frames: self._reference_frames_saved(reference_id, frames),
            )
        )

    def _reference_frames_saved(self, reference_id: str, frames: list[dict]) -> None:
        if self.reference and self.reference["id"] == reference_id:
            self.frames = frames
            self.render_frames.refresh()

    @stable_client
    async def open_reference(
        self,
        reference: dict,
        *,
        restoring: bool = False,
        selected_frame_id: str | None = None,
    ) -> None:
        if self.busy and not restoring:
            return
        if not restoring and self.reference and self.reference["id"] == reference["id"]:
            return
        if not restoring:
            self.busy = True
        try:
            await self.load_reference(
                reference, seek_animal=not restoring, selected_frame_id=selected_frame_id
            )
        finally:
            if not restoring:
                self.busy = False
                self.refresh_sample_ui()
                self.render_sample_navigation.refresh()

    async def load_reference(
        self,
        reference: dict,
        *,
        seek_animal: bool = True,
        selected_frame_id: str | None = None,
    ) -> None:
        video_id = self.video["video_id"]
        previous_skip = self.selected_skip_reason
        previous_frame = next(
            (frame for frame in self.frames if frame["id"] == self.selected_frame_id), None
        )
        path = reference_path(reference)
        if not path.is_file():
            ui.notify(t("video_not_found", path=str(path)), type="warning")
            return
        probe = await run.io_bound(_probe_video, path)
        if not probe.is_valid or not probe.is_web_safe or not probe.duration_sec:
            ui.notify(t("distance_reference_format"), type="warning")
            return
        if not any(row["id"] == reference["id"] for row in self.references):
            return
        frames = await run.io_bound(self.dp.list_reference_frames, reference["id"])
        if not self.video or self.video["video_id"] != video_id:
            return
        if seek_animal:
            if previous_skip:
                selected_frame_id = previous_skip
            elif previous_frame and frames:
                selected = min(
                    frames,
                    key=lambda frame: abs(frame["distance_m"] - previous_frame["distance_m"]),
                )
                selected_frame_id = selected["id"]
        selected = next((frame for frame in frames if frame["id"] == selected_frame_id), None)
        if selected is None and selected_frame_id not in {choice["id"] for choice in SKIP_CHOICES}:
            selected = min(frames, key=lambda frame: frame["distance_m"], default=None)
            selected_frame_id = selected["id"] if selected else None
        if self.reference_player:
            try:
                ready = await switch_source(
                    self.reference_player,
                    self.media_url(path),
                    reference_frame_offset(selected) if selected else 0,
                )
            except (ValueError, TimeoutError):
                ready = False
            if not ready:
                self.reference_load_failed()
                return
        # Only expose candidate evidence after the source has loaded and sought.
        self.reference = reference
        self.reference_duration = probe.duration_sec
        self.selected_frame_id = selected_frame_id
        self.frames = frames
        if seek_animal:
            self.seek_sample()
        if self.reference_player:
            self.render_reference_controls.refresh()
        else:
            self.render_references.refresh()
        self.refresh_sample_ui()

    def reference_load_failed(self) -> None:
        if self.reference_player is None:
            return
        stop_player(self.reference_player)
        self.reference_player = None
        self.reference = None
        self.reference_duration = 0.0
        self.frames = []
        self.selected_frame_id = None
        self.render_references.refresh()
        self.refresh_sample_ui()
        ui.notify(t("distance_media_error"), type="warning")

    def seek_reference(self) -> None:
        frame = next((row for row in self.frames if row["id"] == self.selected_frame_id), None)
        if frame and self.reference_player:
            seek_paused(self.reference_player, reference_frame_offset(frame))

    def preview_frame(self, frame: dict) -> None:
        if self.busy:
            return
        self.selected_frame_id = frame["id"]
        if self.animal_player:
            if self.sample_offset is not None:
                self.seek_sample()
            else:
                pause_programmatically(self.animal_player)
        self.seek_reference()
        self.render_frames.refresh()
        self.refresh_sample_ui()

    @stable_client
    async def refresh_references(self) -> None:
        if self.busy:
            return
        self.busy = True
        try:
            await run.io_bound(self.dp.repair_distance_camera_layout, self.project_id)
            self.references = await run.io_bound(
                self.dp.get_distance_reference_videos, self.video["video_id"]
            )
            updated = next(
                (
                    row
                    for row in self.references
                    if self.reference and row["id"] == self.reference["id"]
                ),
                None,
            )
            if self.reference and updated is None:
                stop_player(self.reference_player)
                self.reference_player = None
                self.reference = None
                self.frames = []
                self.selected_frame_id = None
                self.render_references.refresh()
            else:
                self.reference = updated
                self.render_reference_controls.refresh()
            self.refresh_sample_ui()
        except ValueError as exc:
            ui.notify(str(exc), type="warning")
        finally:
            self.busy = False
            self.refresh_sample_ui()
            self.render_sample_navigation.refresh()


async def setup_distance_review() -> None:
    from review_app.app.entry_point import shared_header

    left_drawer, _ = shared_header(
        show_drawer=True, distance_shortcuts=True, distance_interface=True
    )
    left_drawer.props("behavior=desktop").classes("review-sidebar distance-filter")
    # Register styles before the first await: NiceGUI may send the initial page
    # while database work is still running, before the browser socket connects.
    ui.add_css((Path(__file__).with_name("styles.css")).read_text())
    dp = await get_or_create_data_provider()
    project_id = get_active_project_id()
    if not dp or not project_id:
        ui.label(t("distance_choose_project")).classes("text-grey-6 q-pa-lg")
        ui.button(t("go_to_overview_btn"), on_click=lambda: ui.navigate.to("/overview"))
        return
    await run.io_bound(dp.repair_distance_camera_layout, project_id)
    species_options = await run.io_bound(
        dp.get_distance_species_options, project_id, get_language()
    )
    tags = await run.io_bound(dp.get_all_tags)
    page = DistanceReviewPage(dp, project_id, species_options, tags)
    page.behavior_options = await run.io_bound(dp.get_distance_behavior_options, get_language())
    all_videos = await run.io_bound(dp.get_distance_video_queue, project_id)
    page.camera_options = sorted({row["camera_id"] for row in all_videos if row["camera_id"]})
    if page.camera_filter not in page.camera_options:
        page.camera_filter = None
    await page.load_queue(restore=True)
    await page.load_video()
    with left_drawer:
        page.render_filters()
    page.right_drawer = (
        ui.right_drawer(value=True)
        .props("behavior=desktop width=400")
        .classes("q-pa-sm review-sidebar")
    )
    with ui.column().classes("distance-workspace"):
        page.render_workspace()
    with page.right_drawer, ui.column().classes("distance-sidebar"):
        page.render_sidebar()
    ui.run_javascript("""
        const header = document.querySelector('.q-header');
        if (header) {
            const measureHeader = () => document.documentElement.style.setProperty('--q-header-offset', header.offsetHeight + 'px');
            measureHeader();
            new ResizeObserver(measureHeader).observe(header);
        }
    """)
    sample_navigation_event = f"distance-sample-nav-{uuid.uuid4().hex}"

    @page.client_handler
    async def navigate_sample(event):
        if page.busy:
            return
        direction = event.args.get("direction") if isinstance(event.args, dict) else None
        if direction in ("previous", "next"):
            await page.move_sample(-1 if direction == "previous" else 1)

    ui.on(sample_navigation_event, navigate_sample)
    keyboard_script = (
        Path(__file__)
        .with_name("keyboard.js")
        .read_text()
        .replace("__DISTANCE_SAMPLE_NAV_EVENT__", sample_navigation_event)
    )
    ui.run_javascript(keyboard_script)
