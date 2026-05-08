# To-dos

## Queue & Filtering

### Fix "not annotated / review later" filter - done

The queue in `video_queue.py` computes a derived `is_annotated` flag via SQL CTE, and `review_later` is a column on `VideoLabel`. The bug likely involves an interaction between these two — e.g. a "review later" video is being counted as annotated (because a `VideoLabel` row exists), causing it to vanish from the "not annotated" queue. Fix is a targeted SQL logic change.
**Effort: S**

### Don't disappear annotated videos from the "not annotated" queue - done

Right now the queue is rebuilt after every submission, which removes the just-annotated video from a "not annotated" filter immediately. The request is to keep the current video in place even after saving — only update queue position when explicitly moving to the next video. Requires decoupling queue refresh from annotation save.
**Effort: M**

### Sort queue by camera and video date/time

The `Video` model already has `recorded_at` and `camera_id`. Sorting by camera exists, but date/time sort within camera may be missing. Would add a new sort option to the queue SQL `ORDER BY` clause and expose it in the filter UI.
**Effort: S**

---

## Video Playback

### No mute as default - done

The `autoplay/muted` state is stored in NiceGUI app storage and honored on video load. The default is currently muted (safe browser behavior). Changing this is a one-line default value change, though browsers may block unmuted autoplay — may need a user click to unmute on first load.
**Effort: XS**

### Limit speed to max 2x - done

The player has a speed dropdown with 15 options up to 10x (defined in `video_player.py`). Just remove values above 2x from that list. Zero risk of side effects.
**Effort: XS**

---

## Annotation UI

### Fix species selector width

The species selector re-renders on selection change, causing the dropdown to resize based on content. Needs a fixed `min-width` CSS rule applied to the NiceGUI select component. Likely a one-liner in `annotations.py`.
**Effort: XS–S**

### Fix "Mark as empty and next" button overflow in French

The button text is longer in French (`t()` translation). The button needs either a `max-width` + text truncation, a shorter French translation, or to be restyled to wrap gracefully. Quick CSS or translation fix.
**Effort: XS**

### Add multiple individuals per observation (e.g. "5 chimps seen")

Currently `IndividualObservation` stores one species + behavior + time range with no count. This needs: a DB migration to add a `count` integer column, a numeric input in the annotation UI per observation row, updated export to include the count column, and a design decision about what "count" means (individuals seen simultaneously vs. over the clip). The backend design question is the main complexity — how does this interact with model predictions that are per-species but not per-count?
**Effort: L**

### Comment box per video

No free-text notes field exists on `Video` or `VideoLabel`. Needs a DB migration (add `notes TEXT` column to `VideoLabel`), a textarea in the review UI, and a new export column. Straightforward but touches multiple layers.
**Effort: M**

### Video tags (fire, nice shot, broken metadata, custom)

Consolidated into a single `VideoTag` system — see [decision doc](video-tags.md).
**Effort: M–L**

### "Are you sure?" popup when creating a new tag

When a user creates a custom behavior/species from the annotation UI, it's immediately persisted. Add a NiceGUI confirmation dialog before committing. Low-risk UI change.
**Effort: S**

---

## Data & Export

### Export: change model blank/non-blank column to raw probability

The export pivots `ModelAnnotation` rows to wide format with `model_name__annotation_type` column headers. Currently the `blank_non_blank` column likely shows the predicted class label (`"blank"`/`"non_blank"`) rather than the raw probability float. The `probability` field exists on `ModelAnnotation` — just change which field gets pivoted into the value vs. a separate `__prob` column.
**Effort: S**

### Remove timestamps from behavior export

The export includes `labeled_at` per observation. Either drop the column entirely or make it a filter option. One-line change to the `SELECT` / column list in the export SQL.
**Effort: XS**

### Import historical manual annotations

The `ImportMixin` already handles annotation CSV import (matching by path/stem, mapping species/behavior to IDs). "Historical" likely means a different source format — e.g., annotations from a previous tool or spreadsheet. Effort depends on how different the format is; may need a mapping/transform step added to the import flow.
**Effort: M** (highly dependent on source format)

### Import/export multiple annotator files, merge, re-export

Currently export is per-project and import is append/override per-video. Supporting multiple annotator CSVs requires: a merge strategy for conflicting labels (who wins? majority vote? flag disagreement?), UI to upload multiple files, and a combined export. This is the most complex item on the list — it's essentially a new data pipeline.
**Effort: XL**

---

## Video Management

### Add video metadata display

FFprobe already runs at sync time and stores `duration_sec`, `camera_id`, `recorded_at`. "More metadata" likely means resolution, codec, framerate — these could be stored on `Video` and displayed in a details panel. DB migration + sync logic change + UI display.
**Effort: M**

### More testing on adding/removing videos and subfolders

`sync_videos()` walks `ProjectDir` entries and reconciles with existing `Video` records. Edge cases: symlinks, subfolders added mid-session, videos moved between cameras. This is a testing/QA task more than a code task — but might surface bugs that need fixing.
**Effort: S** (as code task, M if bugs surface)

---

## Effort summary

| Effort | Items |
|--------|-------|
| XS | Mute default, speed cap, button overflow, remove timestamps |
| S | Filter bug, species width, confirm popup, sort by date, export probability, sync testing |
| M | Queue refresh behavior, comment box, video metadata, historical import |
| L | Multiple individuals, video tags |
| XL | Multi-annotator merge pipeline |
