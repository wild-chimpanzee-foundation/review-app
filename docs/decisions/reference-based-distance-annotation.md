# Reference-Based Distance Annotation

**Status:** Current approach. As of 2026-10-12, this workflow replaces the
[depth-estimation proposal](./distance-annotation.md) for now.

## Approach

Annotators estimate an animal's distance from the camera by selecting the closest
matching reference frame. Reference frames are preselected frames from videos in
which a patrol team member holds a sign showing a known distance at the appropriate
position. A camera may have multiple reference videos.

Reference videos are assigned by `(project_id, camera_id)`, which uniquely identifies
a deployment. All animal videos in that deployment share the same reference
collection.

Selecting a reference frame supplies the distance used to determine the exported
distance bin. The annotation interaction is frame selection.

![Distance annotation mockup](./images/distance-mockup.jpg)

## Confirmed Decisions

- Annotators select a reference frame to assign distance.
- Sampling uses every even second of the camera's recorded timestamp, rather than
  every two seconds from the start of playback.
- Each individual, including an infant, has a persistent ID within the video.
  An infant keeps its ID when it leaves its mother and is annotated separately.
- Skipped samples are stored internally as reviewed, with a reason, and omitted
  from the exported dataset. They remain distinguishable from unreviewed samples.
- Distance individuals and samples are separate from `individual_observations`.
  Distance annotation is performed for selected species, rather than every species
  covered by ordinary review.
- The existing `(project_id, camera_id)` identifies a deployment. Reference videos
  are assigned using that pair; no separate deployment table is required.
- Each annotated sample stores its selected reference frame, preserving which
  reference video was used to confirm the distance.
- Age, sex, and tags belong to the distance individual. Behavior belongs to the
  sample. Tags are included; examples include injured and skin disease.
- Each distance individual records `first_seen_offset_ms` and may have a point or
  bounding box identifying the animal on that frame.

## Annotation Workflow

1. Select the individual to follow through the video.
2. Pause at the first eligible sample: the opening frame if its recorded second
   is even, or the next even second otherwise.
3. Compare the animal frame with the reference frames and select the closest match.
4. Set or update the individual's age, sex, and tags; record behavior for the sample
   and confirm the distance.
5. Play from the annotated sample and pause at the next even second. Repeat until
   the individual has been reviewed throughout the video.
6. Adding a new individual assigns an incremental ID and restarts the video from
   the beginning. Complete all individuals before moving to the next video.

For example, a video beginning at `16:02:49` is first sampled at `16:02:50`, one
second into playback. A video beginning at `16:02:48` is sampled at its opening
frame, then at `16:02:50`, `16:02:52`, and so on.

Annotators can play, seek backward, or seek forward to inspect the animal between
samples. Returning to reference selection restores the animal video to the sample
currently being annotated.

### Reference Browsing

- Show a panel containing thumbnails from the beginning of each reference video
  associated with the animal video's `(project_id, camera_id)`. Support more than
  ten reference videos.
- Clicking a video thumbnail enlarges and plays that reference video.
- Provide keyboard shortcuts to jump through a reference video; the proposed jump
  interval is five seconds.
- Allow selection of a preselected reference frame showing a known distance.

### Individuals and Carried Infants

Annotators create individuals during review and initially identify them by their
incremental number. Age and sex may remain unset until entered. Record the frame
where the individual is first seen; optionally click a point or draw a bounding
box on that frame to distinguish it from other animals. Selecting the individual
can return to that frame and show its identifying marker.

A “carries an infant” checkbox creates two observations for the sample: one for
the mother and one for the infant, using the same distance. The infant observation
has age `Infant` and sex `Unknown` by default.

Assign the infant its own persistent ID and reuse it at subsequent samples.
When the infant leaves its mother, annotate it separately using that same ID.
The carried-infant relationship applies to the relevant sample.

### Skipped Samples

Provide a pass button when the selected individual is absent or its midpoint is
not visible, such as when only an arm is visible. Store the skip against the video,
individual, and sample timestamp, with the reason `individual absent` or
`midpoint not visible`.

A skipped sample counts as reviewed and advances to the next even second. It
produces no observation row in the exported dataset.

## Database Schema

This is the agreed schema direction; database models and migrations have not yet
been implemented.

### Existing Tables

- `videos` already provides `video_id`, `project_id`, and `camera_id`. The pair
  `(project_id, camera_id)` identifies the deployment and determines available
  references. It is shared by multiple videos and is not unique on `videos`.
- `individual_observations` continues to serve ordinary review. Distance
  individuals do not depend on or link to these rows.
- Reuse `species`, `behaviors`, `tags`, and annotator metadata. Add separate
  junction tables for distance behavior and tags; existing `observation_tags`
  associates ordinary observations with behaviors, and `video_tags` applies to
  whole videos.

### New Tables

| Table | Main fields | Purpose |
|---|---|---|
| `reference_videos` | `id` (UUID), `project_id`, `camera_id`, `video_path` | Reference video files assigned to a deployment. |
| `reference_frames` | `id` (UUID), `reference_video_id`, `offset_ms`, `distance_m` | Prepared, selectable frames with known distances. |
| `distance_individuals` | `id` (UUID), `video_id`, `individual_number`, `species_id`, `age`, `sex`, `first_seen_offset_ms`, optional anchor fields, annotation metadata | One animal followed within an animal video; includes infants. |
| `distance_samples` | `id` (UUID), `individual_id`, `offset_ms`, `status`, `skip_reason`, `reference_frame_id`, `distance_bin`, `carried_by_individual_id`, annotation metadata | One individual's annotated or skipped result at a scheduled timestamp. |
| `distance_individual_tags` | `individual_id`, `tag_id`, tagging metadata | Tags applying to the distance individual across its samples. |
| `distance_sample_behaviors` | `sample_id`, `behavior_id` | Behavior labels at a particular sample, using the existing vocabulary. |

Foreign keys link reference videos to projects, reference frames to reference
videos, distance individuals to animal videos and species, and samples to distance
individuals and selected reference frames. The tag and behavior junctions reference
their respective existing vocabulary tables.

The UUID is the durable identity; `individual_number` is the incremental number
displayed and exported within a video. Annotation metadata includes `labeled_by`,
`labeled_at`, and `updated_at`; tagging metadata includes `tagged_by` and
`tagged_at`.

### First-Seen Frame and Individual Anchor

Store the identifying frame and optional geometry on `distance_individuals`:

| Field | Meaning |
|---|---|
| `first_seen_offset_ms` | Integer playback offset of the frame where the individual is first seen. |
| `anchor_x`, `anchor_y` |  bounding box's top-left corner. |
| `anchor_width`, `anchor_height` | Bounding box dimensions |

Coordinates are normalized to the original video's width and height, in the range
0–1, with the origin at the top-left. Undo display zoom/pan and account for any
letterboxing before storing them. This allows the marker to be displayed at
different player sizes.

The anchor always refers to the frame at `first_seen_offset_ms`, which may occur
after the opening frame and does not need to be an even-second distance sample.
Changing that offset requires clearing or replacing the anchor for the new frame.
The anchor helps annotators identify the individual and return to its first-seen
frame; following it across later frames remains manual.

Require an offset within the video's duration. For a point, require `x` and `y`
within 0–1 and null width/height. For a bounding box, require positive width/height
and the entire box within 0–1. With no anchor, all geometry fields are null.

### Sample State and Timing

- No sample row at an expected timestamp means unreviewed.
- `status = annotated` requires a selected reference frame and distance bin, with
  no skip reason.
- `status = skipped` requires a skip reason, with no selected frame or distance
  bin. It counts as reviewed and is excluded from observation exports.
- `offset_ms` stores an integer playback offset from the beginning of the video.
  Sampling follows even seconds of the camera clock. Exported observation time is
  the recorded start time plus that offset; it is distinct from `labeled_at`.
- Keep both `reference_frame_id` and the assigned `distance_bin` on annotated
  samples. The frame's `distance_m` describes the reference's known distance; the
  bin records the animal's assigned distance interval.

### Constraints and Reference Provenance

- Require unique `(video_id, individual_number)` on distance individuals.
- Require unique `(individual_id, offset_ms)` on distance samples.
- Require unique `(reference_video_id, offset_ms)` on reference frames and unique
  `(project_id, video_path)` on reference videos.
- Each junction table uses its two foreign keys as a composite primary key.
- Require project and camera values for reference assignment, and ensure the
  selected reference frame's source video matches the animal video's project and
  camera. These values currently allow nulls on `videos`, so missing metadata must
  be resolved before reference-based distance annotation.
- Reference videos used for an animal video are obtained through
  `distance_individuals → distance_samples → reference_frames → reference_videos`.
  The frame foreign key preserves the exact reference selection for each sample;
  no additional per-video reference association table is needed for selected
  references.

For a carried infant, `carried_by_individual_id` is set on the infant's sample and
references the mother. Both individuals must belong to the same video, and an
individual cannot carry itself. Confirming the sample writes the mother and infant
rows together at the same offset, using the same selected frame and distance bin.
The relationship applies only to that sample; the infant retains its own identity
when annotated separately.

## Exported Dataset

Export one row per annotated distance sample. Join the individual's species, age,
sex, number, and tags into each row; include the sample's behavior and distance
bin. Skipped samples produce no rows. Ordinary `individual_observations` are not
the source of this export.

### Annotator-Provided Fields

| Field | Values or source |
|---|---|
| Age | Adult, Adolescent, Juvenile, Infant; stored on the distance individual. |
| Sex | Male, Female, Unknown; stored on the distance individual. |
| Distance bin (meters) | 1–2; 2–3; 3–4; 4–5; 5–6; 6–7; 7–8; 8–10; 10–12; 12–15; 15–20; 20–25; >25. Derived from the selected reference frame. |
| Behavior | Inspection, Staring at camera, Run away, Getting closer, Stop, Rest, Play, Feed, Groom; stored per sample. |
| Tags | Included at distance-individual level; examples include injured and skin disease. The available values remain to be defined. |

### Automatically Populated Fields

- Individual ID
- Annotator
- Folder_name
- Location_ID
- Video name
- Year, Month, Day, Hour, Min, Sec of the sampled camera timestamp
- Species from the distance individual

## Suggested Implementation Approach

The following are proposed defaults and next steps, rather than confirmed product
decisions. The schema direction is sufficient to begin backend work; settle these
details before completing the annotation workflow.

### Reference Preparation and Recording Time

- Provide a setup screen to register reference videos by project and camera, open
  a video, select reference frames, and enter each frame's known distance.
- Use imported recording metadata after checking it against the visible camera
  timestamp. Require correction of missing or inaccurate recording times before
  timed distance annotation. Verify whether `videos.created_at` can serve as that
  source or whether a dedicated camera-clock field is needed.
- Define bin boundaries, including exact values such as 2 meters and 25 meters,
  and handling of distances below 1 meter.

### Individual Selection, Resume, and Completion

- Show a list of manually created individuals. Initially label entries by number;
  add age and sex to the display when entered. Adding an individual selects it.
  Use the first-seen frame and optional anchor to help recognize the animal.
- Add `review_status` (`not_started`, `in_progress`, or `completed`),
  `completed_by`, and `completed_at` to `distance_individuals`.
- Store the active selection in a proposed `distance_review_progress` table with
  `video_id`, `annotator_name`, `active_individual_id`, and `updated_at`, keyed by
  `(video_id, annotator_name)`. Require the active individual to belong to that
  video. Each annotator can resume their own selection.
- On opening or switching individuals, resume at the next unreviewed scheduled
  sample. Completed individuals remain available for inspection and correction.
- Display progress such as “8/15 samples reviewed,” counting annotated and skipped
  samples. An explicit **Finish individual** action checks that every scheduled
  sample has been reviewed before marking the individual completed.
- Provide an explicit **Finish video** action and persist distance-review
  completion separately from ordinary review. Completing all listed individuals
  does not establish that every animal has been added. Finalize the video-level
  completion schema and rules for reopening completed reviews.
- When marking a carried infant, allow selecting an existing infant or creating
  one, so the same infant is reused across samples.

### Review Page and Transfer

- Provide a dedicated distance-review page using the existing video player, with
  a species filter and progress separate from ordinary review.
- Keep the research CSV described above focused on annotated observations.
- Provide a separate transfer bundle that preserves skips, durable individual
  identities, first-seen anchors, individual attributes and tags, sample behavior,
  reference selections, and completion state. Define how reference metadata and
  media paths are transferred so selections resolve when imported elsewhere.

### Example Data and Implementation Order

Use a small representative deployment during development: an animal video, its
reference videos, and trustworthy recording metadata. Multiple visible animals
and a carried infant would help exercise identity and relationship handling.

Start a feature branch with this specification and its mockup assets, then proceed
in this order:

1. Schema, migrations, and storage operations, including the finalized progress
   and completion fields.
2. Reference-video registration and frame preparation.
3. Individual creation, first-seen anchors, selection, and resume.
4. Timed samples, skips, infant handling, and completion.
5. Research export and transfer bundles.

## Open Questions

- Confirm handling of distances below 1 meter and which bin includes each shared
  boundary.
- How are reference frames and their known distances prepared and imported?
- Establish a reliable recorded start time matching the camera clock. The existing
  `videos.created_at` comes from imported or probed metadata; verify whether it
  provides that time and how to handle missing or inaccurate timestamps.
- Define explicit completion tracking for individuals and the whole distance
  review, using the suggestions above as a starting point; include reopening rules.
- Confirm the dedicated review page, species filtering, and transfer-bundle scope.
- Define how to handle edits or removal of references already used by samples,
  preserving the evidence for existing annotations.
- Confirm the keyboard jump interval for reference videos.
- Which individual tag values should be available?
- If model suggestions are introduced later, how should they assist reference
  selection? They are not required by the current workflow.
