# Distance Annotation via Depth Estimation

**Blocker: Validate Depth Anything output against reference videos.**

## Context

Wildlife camera trap videos are reviewed and annotated for species and behavior. A future need is to also record the **distance of the animal from the camera** at the time of observation. This document captures the planned approach.

## Approach

Use **Depth Anything 3** (metric depth estimation) to generate per-video depth maps externally, import them into the app alongside AI annotation CSVs, and expose a dedicated **distance estimation mode** in the video player for click-based distance annotation.

## Depth Map Import

Depth maps are produced **outside the application** (like AI annotation CSVs) and imported by the user. The DB associates each depth map file with a video via a `depth_map_path` column on the `videos` table (or a separate `depth_maps` table if multiple models per video are needed).

**Open question:** Depth Anything 3's output format needs to be confirmed before finalizing the import format and extraction pipeline. Options and tradeoffs:

| Format | Precision | Size vs. original video | Seekable |
|---|---|---|---|
| 8-bit grayscale MP4 | 256 depth levels (coarse) | ~1/4–1/3 | Yes, via existing media stack |
| 16-bit grayscale video | 65 536 levels (good) | ~1/3–1/2 | Codec support patchy |
| Per-frame EXR / float32 NPY | Full float precision | 10–50× larger | No (frame-by-frame only) |

Recommendation: 16-bit single-channel PNG sequences or a 16-bit grayscale video. The chosen format must declare its **depth encoding scale** (e.g. value range → meters) either in the file or a sidecar metadata file, so the app knows how to convert pixel values to metric depth.

## UI/UX: Distance Mode

### Entering the mode

A toggle button in the existing video player toolbar (alongside speed/brightness/fullscreen). When active:

- Cursor changes to a crosshair
- A semi-transparent colorized depth overlay (e.g. viridis/plasma colormap) appears on the video
- A depth scale legend (e.g. 0–20 m) is shown in the corner

### Overlay rendering

Do **not** use two synchronized `<video>` elements — drift between them is unavoidable. Instead: render the depth map as a **canvas overlay** that updates on each `timeupdate` tick by extracting a single frame from the depth file at the current timestamp. This fits naturally into the player's existing canvas-based zoom/pan layer and avoids sync issues entirely.

### Sampling interaction

Auto-pausing the video every 2 seconds; the user can also step manually using existing keyboard shortcuts and click each time.

## Distance Extraction (per click)

1. **JS captures click coordinates** relative to the displayed `<video>` element in CSS pixels.
2. **Invert the current zoom/pan transform** (already tracked by the player) to get the click position in actual video pixel space. Normalize: `x_frac = actualX / videoWidth`, `y_frac = actualY / videoHeight`.
3. **Send to backend:** `(video_id, timestamp_sec, x_frac, y_frac)`.
4. **Backend extracts depth value:**
   - Seek to `timestamp_sec` in the depth map file (one ffmpeg call: `-ss {t} -i depth.mp4 -frames:v 1 -f rawvideo pipe:1`)
   - Read raw pixel at `(x_frac × depth_width, y_frac × depth_height)`
   - Apply encoding scale → metric depth in meters
5. **UI shows result immediately:** labeled dot at the click location with depth in meters, appended to a list of distance observations for that video.
6. Do we allow the user to manually correct annotations?

## Data Model

New table `distance_observations`:

| Column | Type | Notes |
|---|---|---|
| `id` | integer PK | |
| `video_id` | FK → videos | |
| `timestamp_sec` | float | Playback time of the click |
| `x_frac` | float | Click x as fraction of video width |
| `y_frac` | float | Click y as fraction of video height |
| `depth_value_m` | float | Metric depth in meters |
| `labeled_by` | text | Annotator name |
| `labeled_at` | datetime | |

Optionally link to `individual_observations` to associate a distance reading with a specific species annotation.
