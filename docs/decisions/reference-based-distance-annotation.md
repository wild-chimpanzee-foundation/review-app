# Reference-Based Distance Annotation

**Status:** Implemented workflow at `/distance-review`. This is the authoritative
workflow/design document; it replaces the earlier
[depth-estimation proposal](./distance-annotation.md). Versioned AI Distance ZIP
input is supported via Model Import → AI Distance (see the
[package format and current limitations](../../userdocs/importing.md#ai-distance-packages)).
AI predictions remain separate from human evidence. Their supplied playback times
are retained exactly, with no manual clock verification required. Imports create
full-video AI drafts with inferred Absent recommendations at missing samples, and
original animal masks can be overlaid at prediction frames. Research export and transfer remain deferred. See the [cleanup plan](./distance-branch-cleanup-plan.md) for
migration consolidation, development-database recovery, and validation status.

## Approach

Annotators estimate an animal's distance by selecting a reference frame showing
a patrol team member at a known distance. Reference frames can be calibrated on
demand during review; upfront preparation is optional. The chosen frame supplies
the sample's exact distance in meters, including existing fractional distances.
There is no free-form distance-entry field during animal annotation.

Distance review uses one canonical task per video and target species, created
idempotently from manual/confirmed Species Review labels, without requiring an
ordinary Review completion flag. Model predictions alone do not establish eligibility.
Each task owns its alignment, individuals, samples, resume state, and completion.
Different species tasks for the same video appear separately and finish independently.
Source label corrections do not automatically delete existing tasks or change their
species. Distance evidence never automatically corrects ordinary Review labels.

![Distance annotation mockup](./images/distance-mockup.jpg)

## Annotation Workflow

1. Import reference clips in a deployment's paired `references/` and `videos/`
   layout to tag them **Reference Video** automatically, or tag other reference
   clips manually in ordinary Review. Open **Distance**; reference distances may
   initially have no configured frame positions.
2. Select a species task in the Distance queue; its target species stays visible.
   Align in the independent task clock dialog. The default Simplified workflow opens
   near the video midpoint: select any frame showing an even camera second.
   It shows the expected timestamp as an estimate; correcting that time is optional.
   Without an explicit timestamp edit, absolute camera time stays
   unknown. There is no frontend alignment-mode selector.
3. Add an individual on its identifying first-seen frame. Its species is fixed to
   the task target; draw a bounding box. Earlier scheduled samples are saved as **Absent**; samples at
   or after first sighting remain unreviewed. Creation retains the identifying
   frame while selecting the first scheduled sample at/after it, or the final
   sample when first sighting is later than the last stop.
4. At each scheduled sample, select a reference distance, **Not visible**, or
   **Absent**; set sample behaviors and any carried infant, then confirm. If a
   distance is not yet configured, first pause its reference video on the matching
   frame and click that distance button to save the reference mapping.
5. Saving plays the consecutive interval and pauses at the next scheduled sample,
   even if that sample was already reviewed. Confirming/skipping the final sample
   retains it and shows **End of video**; it never switches individuals, returns
   to earlier gaps, or finishes/advances the video automatically.
6. Review other individuals manually. Full sample coverage completes each
   individual automatically. Use **Finish task** explicitly after all target-species
   animals have been added and all individuals are complete. An empty task requires
   **No target species visible**, with explicit rejection of pending AI suggestions.

Selecting a reference only changes the current form, not saved evidence. Existing
samples remain editable; a missing sample row means unreviewed, not absent.

### Camera clock and sampling

- **AI-imported schedules take precedence.** Retain every supplied prediction time
  exactly. Infer the dominant within-track cadence and fractional phase for each video,
  include the opening frame, and fill gaps through the video end (exclusive). Nearby
  frame-quantization jitter occupies the inferred stop rather than creating a duplicate.
  Each imported individual gets a pending sample at every stop; missing predictions
  recommend Absent, never human-reviewed absence. At least two predictions on a track
  in each video are required to infer cadence. No snapping, even-second validation,
  or clock verification is required. Absolute camera time remains unknown.
  Manual-only videos retain the alignment workflows below.
- A different package may replace only untouched AI drafts, with a preview warning
  and explicit confirmation. Manual configuration, human edits, started reviews, and
  completed reviews block import regardless of whether the times agree. Import
  rechecks these guards inside its transaction; confirmation never authorizes
  deleting human work. Reference calibration and original package provenance survive
  draft replacement. Identical package reimports are always no-ops, including after
  corrections, deletions, or later imports; old packages cannot switch active drafts
  or restore earlier schedules.

- New and explicitly reset alignments default to Simplified. Exact is retained
  only as a code configuration option: set `DISTANCE_CLOCK_ALIGNMENT_MODE = "exact"`
  in `review_app/app/config.py` to opt into that calibration workflow. No frontend
  control exposes mode selection, and Exact-specific calibration controls are not
  rendered under the default configuration. Saved reviews retain their schedules
  and timestamp-correction semantics regardless of the configured default; only
  an explicitly confirmed reset applies the configured workflow to an old review.
- In **Simplified alignment**, the dialog opens near the midpoint, or at the saved
  anchor. Bold **Choose even frame** text and frame controls appear directly below
  the player. Select a frame showing an even camera second, then press **Frame shows
  an even camera second.**; no checkbox or exact transition selection is required.
  The long gray hint and separate expected-timestamp confirmation button are omitted.
  Schedule every two seconds backwards and
  forwards from its precise playback offset, excluding samples outside the video.
  For example, an anchor at playback `5.120125 s` yields samples at `1.120125`,
  `3.120125`, `5.120125`, `7.120125`… seconds.
- Show the expected camera timestamp for the displayed frame, using an existing
  verified clock or file metadata as a suggestion. Label it as an estimate and
  update it during inspection. Prefill the optional 24-hour date/time field
  with calendar/time pickers (including seconds) with that expected time. Leave it
  unchanged to align frames only, or edit it to establish camera time. Calendar/time
  picker changes and ±1 s adjustments count as edits. Pressing the even-frame button
  alone does not verify an untouched estimate; previously confirmed camera time is
  retained on reconfirmation. Otherwise camera time remains unknown and sample labels
  show playback offsets. Moving frames refreshes the estimate and discards the prior
  frame's optional correction. A frame change during capture requires checking the
  new frame and pressing the confirmation button again.
- Previously saved parity-only Simplified reviews retain their old 0/2/4… or
  1/3/5… grid. They are never silently converted to a midpoint anchor. Use the
  confirmed reset workflow to change them. Compare both current modes on real
  clips; a single anchor does not correct irregular frames or camera-clock drift.
- In **Exact alignment**, sample every even second of the camera's timestamp, not
  every two playback seconds. A clip starting at `16:02:49` samples at camera
  `16:02:50`, `:52`, `:54`. The following timestamp/anchor rules apply to Exact.
- Select the first frame showing an odd-to-even transition and enter that even
  whole-second camera time. Confirmation pauses/captures the displayed frame;
  there is no additional frame-selection/verification action.
- Mark whether the opening still already shows an even second. If so, that still
  is also required, and alignment normally uses the next odd-to-even transition.
  A short clip ending before a transition may use its even opening still.
- Alignment is shared across individuals/annotators within one species task. After verification, timestamp-only
  corrections must be whole multiples of two seconds and preserve the exact anchor,
  opening-even setting, and scheduled playback offsets. They retain all annotations,
  progress, and completion, and update verification attribution/time.
- Changing the saved alignment mode, alignment frame, or opening-even setting requires explicitly choosing
  **Change alignment frame and reset Distance review** and confirming the destructive
  warning. The reset atomically deletes every Distance individual (including infants),
  box, attribute/tag, sample/behavior, resume state, and current completion for this
  task, including other annotators' work, before saving the new alignment. Other
  species tasks, prepared references, ordinary Review, and completion history remain unchanged. Cancellation or invalid
  alignment leaves the existing review intact. Reconfirming an unchanged Simplified
  anchor (or legacy parity), or explicitly establishing/correcting its camera time,
  preserves evidence and completion. Timestamp corrections retain even-second parity.
- `videos.created_at` is only a suggested time. Store the verified camera wall clock
  without timezone conversion, separately from file metadata. Display camera times
  in 24-hour format. Both alignment modes use prefilled `YYYY-MM-DD HH:mm:ss`
  fields with calendar and explicit 24-hour time pickers, including seconds,
  independent of browser AM/PM preferences. Values represent camera wall time
  without timezone conversion. Adjacent **−1 s / +1 s** buttons adjust
  the timestamp (including minute/date rollover), not the selected video frame.
  In Simplified mode these adjustments explicitly confirm/correct camera time.
  Saving still requires an even camera second; timestamp-only corrections retain
  the existing two-second-step restriction.
- Preserve precise playback anchors independently of integer-millisecond sample
  identities. For example, a `16:02:50` anchor at playback `0.920 s` schedules the
  next camera sample at `2.920 s`, not `3.000 s`.
- In Exact mode, estimate the first frame at/after a sample boundary from reported
  FPS and anchor phase. Probe metadata once per file with a two-second timeout;
  do not scan all frames while loading. Without FPS, use a 40 ms margin shortened
  near clip end. The opening exception remains the opening still.
- Direct Exact sample seeks pad ten microseconds inside the estimated frame to
  avoid its predecessor. Simplified seeks and playback stops preserve the chosen
  frame's precise phase in both directions, without forward FPS rounding or padding;
  legacy parity-only schedules likewise use their unshifted playback offsets.
  Forward adjustments could otherwise cross into the next odd displayed second.
  Saving verifies the displayed position using frame-based tolerance.
  Irregular/dropped frames may still show a neighboring frame; stored sample
  identities and camera timestamps do not change.

### Navigation and inspection

- Scheduled stops are browseable as soon as alignment is saved, before creating
  an individual. The graph shows neutral scheduled markers and highlights the
  selected stop without implying distance evidence. Click markers, use left/right
  sample navigation, or replay intervals to inspect alignment. Loading an aligned
  video with no individuals selects its first stop; deleting the final individual
  returns to schedule-only browsing. Browsing never creates individuals, samples,
  or resume/progress records. Annotation controls and saves still require an
  individual.
- **Play to this sample** replays from the preceding scheduled sample. For the
  first scheduled sample, replay from the video opening when its offset is
  nonzero; omit the action when the first sample is already the opening frame.
  Playback pauses at the selected sample without saving evidence or navigating.
- Manual annotation pauses snap to the most recent scheduled sample at/before
  the pause position. Automatic stops seek the intended next scheduled frame to
  correct callback delay. Programmatic pause events must not retrigger snapping.
- Explicit inspection seeks/frame steps remain unsnapped. Clock and fixed-frame
  box dialogs are exempt. Choosing a reference restores the animal to the
  selected annotation sample without an explicit **Return to sample** button.
- Card selection and **J/K** always pause at that individual's first-seen frame,
  regardless of saved coverage or the previous playback position. The first-seen
  button selects its own individual and works even when already active.
- Opening a saved session instead restores the annotator's active individual and
  next unreviewed sample. Creating an individual keeps its chosen identifying
  frame. These paths deliberately have different navigation rules.
- There are no per-frame or per-individual draft caches. Leaving a sample or
  switching individuals discards unconfirmed distance, visibility, behavior, and
  infant edits. Revisited samples restore saved evidence; new stops prefill the
  preceding confirmed choice and metadata for that individual. Without preceding
  evidence, select the smallest available distance and empty metadata. Neither
  navigation nor prefilling writes or overwrites evidence. Individual attributes
  save immediately and are rendered directly from their saved values.
- Explicit distance/visibility changes immediately preview the current sample in
  the graph: a cyan diamond for distance or a cyan dashed status interval, labeled
  **Unsaved preview**. This is presentation-only; stored samples and completion are
  unchanged until confirmation. Navigation discards the preview; untouched prefills
  and locked carried-infant evidence do not create graph previews.
- Visible samples save at least one behavior, with **Unidentified** used when
  the selection is empty; multiple behaviors are allowed. Behavior prefills are
  drafts until confirmation. Selecting **Absent** or **Not visible** clears and
  disables the behavior field; skipped samples save no behaviors. These rules also
  apply to Apply to end and carried-infant evidence. Schema v27 repairs existing
  visible samples without behaviors and removes behavior links from skips without
  altering provenance, timing, progress, or completion.
- Reference browsing retains sample behaviors, infant choices, visibility choice,
  brightness/contrast, and the animal player. Reuse the reference player on clip
  switches; keep its prior image until the next source has loaded and sought.
  Pages own independent media URLs, released when their client is deleted.
- **Absent** and **Not visible** dim the reference image, including restored
  saved choices. Switching clips preserves dimming. Playing, seeking, scrolling,
  or successfully stepping the reference video clears the visibility selection
  and restores color without changing saved annotations or image settings.
  Programmatic sample restoration does not clear a restored visibility choice.

### Apply to end

**Apply to end** repeats the selected distance/reference or visibility choice,
behaviors, and carried-infant assignment from the current sample through the final
scheduled sample, inclusively, in one transaction. Confirmation names the choice,
individual, starting offset, and affected count, and warns that **all existing
annotations and skips are overwritten**. There is no preserve-only mode.

A new infant is created once and reused across the interval. Other individuals
and earlier samples are unchanged, except for linked infant evidence being
corrected. Tags remain individual-wide. Success selects/pauses at the final sample
for the current individual without finishing the video. Cancellation or failure
retains the previous selection/playback. Ordinary sample corrections remain
available; there is no separate bulk-absence or bulk-undo API.

### Individuals and carried infants

- Each individual has a durable UUID and an incremental displayed number within
  the task. Species is fixed to the task target; age, sex, and tags belong to the
  individual; behavior belongs to each sample. Age/sex may remain unset during
  creation and annotation, but both are required to finish the task. Allowed ages are `Adult`, `Adolescent`,
  `Juvenile`, `Infant`, `Unknown`; sexes are `Male`, `Female`, `Unknown`.
  Explicit `Unknown` satisfies completion; unset values do not.
- Species is read-only in individual cards and fixed in creation dialogs. Saved
  task species remain readable outside the current project vocabulary. Backend
  writes reject cross-species individuals and species changes inside a task.
- Metadata saves immediately. Edits reopen explicit task completion; ordinary Review
  and other species tasks are unaffected.
- Creating an individual requires a complete normalized box on its first-seen
  frame. Geometry is relative to original video dimensions, corrected for zoom,
  pan, and letterboxing, with positive dimensions entirely inside 0–1.
- Add/redraw dialogs start in box mode on an independent fixed-frame player and
  inherit brightness/contrast. Zoom, pan, fullscreen, cancellation, and save
  guards remain available. Redraw changes geometry, not the identifying frame.
- Show all boxes matching the displayed first-seen frame: selected yellow,
  inactive gray, with readable numbers even for coincident parent/infant boxes.
  Outline spacing is display-only; never change saved geometry or show boxes on
  adjacent unrelated frames. Tracking across later frames remains manual.
- A newly carried infant inherits its parent's identifying frame **and box**,
  defaults to `Infant`/`Unknown`, and keeps its own UUID after becoming independent.
  The creation dialog's **Carries an infant** checkbox creates both identities
  atomically, without distance evidence, and preselects that infant for the
  carrier's first sample.
  The infant stores its initial carrier so that this default survives reloads and
  form restoration. It applies from creation until saved carrier evidence takes
  precedence; it is not a permanent carrying assignment for every sample.
  Age, sex, and eligible individual tags are optional creation fields and apply
  only to the carrier, not the automatically created infant.
  Scheduled samples strictly before infant **creation** are absent; this cutoff
  is not the inherited first-seen offset. Do not clone the parent's whole timeline
  or prefill later absence.
- While carrying is active, parent assignments atomically copy evidence,
  visibility choices, and behaviors to that same infant, for manual and suffix
  writes. Both individuals must belong to the same task; self-carrying,
  nested carrying, and simultaneous different carriers are rejected.
- Viewing a carried infant shows its carrier and locks distance/visibility choices for
  that sample; behaviors remain editable. Change carrying on the adult's sample.
  Independent and unreviewed infant samples retain their distance controls.
- Untoggling for later samples does not erase earlier carried evidence. Correcting
  a carrier's earlier sample without its infant removes that linked infant sample
  at that offset, making it unreviewed. Independent infant evidence elsewhere
  survives. Correcting an infant's annotated behaviors keeps its carrier link
  when its reference still matches; assigning different evidence is independent.
- Deleting an individual removes its samples. Deleting a carrier also removes
  infant samples linked to it, not the infant's identity or independent samples.
  Reconcile affected completion in the same transaction.

### Completion and ordinary Review context

Show ordinary Review's target-species count without synchronizing observations.
An individual completes automatically once every scheduled sample is annotated or
explicitly skipped. Normal **Finish task** requires at least one human individual,
complete sample coverage, and age/sex for every individual (explicit `Unknown` is
accepted). Relevant edits/reprobes reopen current completion as needed.

Warn when the confirmed target-species count exceeds the task's Distance count.
Offer **Return to annotation** or **Ignore and finish**; other species tasks do not
generate omission warnings for this task. Successful completion advances to the
next task, even when it uses the same video. Cancellation/failure never advances.

**No target species visible** completes a task without a placeholder individual.
Human individuals block it; pending AI-only identities require explicit rejection,
retained with author/time and original import provenance. The absence outcome
retains the task's source species/count context without modifying Species Review.
Adding human evidence invalidates absence. Any editor may finish a task; current
completion is shared, resume progress is per annotator, and previous completion
claims remain in history.

## References and deployment identity

Reference clips are imported videos with the video-level `reference_video` tag.
They remain in ordinary Review for tagging/conversion and are excluded from the
Distance animal queue. Import and rescan automatically add the tag to clips
inside a recognized paired deployment's `references/` folder, including existing
clips. Existing tags and attribution are preserved; manually removing this tag
is undone on the next rescan while the clip remains in that folder. A standalone
folder named `references` is still an ordinary camera folder. Tags on other clips
are never removed by folder inference. Missing sources or unresolved
project/camera metadata do not become selectable references.

Reference matching uses **project + registered import-root ID + camera label**.
Separate roots both containing `cam_1` are distinct deployments. Prefer the most
specific registered root for nested imports; root relocation preserves its ID.
A single-root project may use explicitly assigned external/relative reference
files. In multi-root projects, unresolved external paths are ambiguous and must
not match by camera label alone. Apply this scope to both discovery and writes.
No separate deployment table is needed.

Paired `references/` and `videos/` folders support both
`cam_a/{videos,references}/clip.mp4` and `{videos,references}/cam_a/clip.mp4`.
In either layout, the category folder is omitted from the camera label, so both
clips have camera ID `cam_a`. Category folders must be paired inside the import
root; importing only `references/` does not infer reference roles from its name.
At an imported deployment root with clips directly in the category folders, use
the root's name; below it, use relative folder paths. Legacy folder-derived labels
are repaired explicitly during Distance setup or **Refresh references**, not as a
side effect of queue queries. Repair only old
inferred labels, preserving explicit assignments, identities, and saved evidence.

Discovery reuses durable reference records by project and normalized path,
including previously registered files. Removing/reapplying a tag does not recreate
prepared frames. Untagging hides unused clips, while references used by the
current video's samples remain visible as saved evidence, labeled no longer
tagged. Use available playback conversions for clips/thumbnails.

### Reference metadata caching

Each Distance page keeps a 64-entry LRU cache of successful reference-video probe
results, keyed by resolved path and validated against nanosecond modification time
and file size on every lookup. Failed probes and files changed during probing are
not cached. Initial reference loading and later switches share this cache; frame
mappings remain freshly queried so edits/deletions are never hidden by caching.

### On-demand reference calibration

The default workflow offers whole-meter buttons from **1–15 m** below the current
reference video (`DISTANCE_REFERENCE_METERS` in `review_app/app/config.py`).
Existing saved distances outside this range, fractional distances, and repeated
legacy reference frames remain accessible without conversion or deletion.
**+ Add distance** beside the buttons opens a small whole-meter input for any
additional distance, including values above 15 m. It suggests the next meter after
the highest default or configured distance (initially 16 m). Enter saves the
current reference frame and adds its persistent button without saving animal
evidence. Existing distances are selected rather than overwritten; failed saves
keep the input open for retry.

- Unconfigured buttons show a **plus** and dashed outline. Pause/scrub the
  reference video (or use its ±1/5-frame controls), then click a distance to save
  the displayed frame's precise playback position and rounded millisecond ID.
- Configured buttons show a **check**. Clicking seeks to the saved frame and
  selects that distance as an unsaved animal-sample choice; it never overwrites
  the reference position. Keyboard distance navigation only visits configured
  references and visibility choices, so it cannot accidentally calibrate frames.
- First-time configuration also selects the new distance as an unsaved choice.
  Neither configuring nor selecting a reference saves animal evidence. The usual
  **Confirm selection and advance** or **Apply to end** action is still required.
- Missing distances do not block starting a review. Mappings persist per reference
  video across animals and sessions; no database rows are created for unknown
  distances. Concurrent first-time configuration reuses an existing distance
  mapping rather than replacing it. Failed saves leave the distance unconfigured.
- The preparation dialog remains available for additional distances and upfront
  calibration. Its explicit **Edit reference frame** action selects an existing
  mapping; **Update reference frame** saves a replacement position while retaining
  the mapping's ID and distance, including links from existing animal samples.
  Canceling an edit leaves the mapping unchanged. Refresh reloads saved mappings
  without rebuilding the reference player.
- Each prepared frame has a red delete action. Its confirmation shows the current
  count of saved animal samples that will be cleared, across all videos. It warns
  that deleting the distance-to-frame mapping clears those samples, including
  behaviors and carrying links, and reopens affected individual/video reviews.
  Individuals, clock alignment, unrelated evidence, and the reference video remain
  intact. Deletion and evidence clearing are transactional; used mappings cannot
  be deleted through the provider without explicit `clear_samples=True`.
  Canceling or a failed deletion preserves the mapping and annotations.

Reference preparation uses an independent dialog player with ±1/5/20-frame
controls and reported FPS. New clips start at playback zero and 1 m. Open existing
clips at the latest prepared frame; show saved distances/timestamps and suggest
the next whole meter, skipping used values. Saving preserves playback and advances
the suggested meter; failure preserves both playback and entered distance.
Reference distances must be finite/positive. Store precise playback offsets
alongside rounded millisecond frame identities; never round saved meter values.

## Desktop UI and shortcuts

- Reuse Review's header and desktop drawers: 300 px filters on the left, 400 px
  individual/completion controls on the right. A sticky expand/collapse button
  at the top of the sidebar switches between its full width and a 72 px rail,
  rather than hiding it. Label it **Collapse sidebar** when expanded and use only
  the expand icon in the narrow rail. The button remains visible in both modes even when the
  page header is hidden; it is not duplicated in the header. The rail retains **A / Add**, each
  individual's number and automatic completion checkbox indicator, and a green
  finish icon. Highlight the active individual; clicking a rail entry selects it,
  never manually changes its completion. Keep add/select/finish guards and show a
  clock-verification action when needed. Tooltips retain full labels. Completed
  individuals in the expanded view have a green checked icon and number, plus a
  green card border when inactive. Preserve the active card's orange border
  independently of completion.
  Switching widths preserves full-form elements, players, active identity/sample,
  and unconfirmed form choices. Mobile compatibility is deferred.
- Keep equally sized animal/reference frames, graph, and essential actions
  visible at 1280×800. Reference clips/distance choices use bounded strips; long
  individual lists scroll inside the right drawer. Allow hiding/restoring the
  Distance header without changing ordinary Review. Comparison panels are frameless:
  no card border, shadow, background, or padding; no outer workspace padding; 4 px
  gaps. Videos use the full column width at their native aspect ratio without a
  viewport/430 px height cap, cropping, or stretching. Fullscreen and dialog players
  retain their separate sizing rules.
- Below the reference player, group distance navigation, distance buttons, and
  visibility choices in a compact toolbar. Behavior, carrying, and infant identity
  use an auto-fitting field grid: share a row when space permits and wrap in
  narrower panels. Show saved/selected summaries inline and use a compact
  horizontal reference-thumbnail browser flanked by **Shift+Tab** (previous) and
  **Tab** (next) controls; omit the redundant reference-clip label.
  **Confirm selection and advance** and **Apply to end** sit together immediately
  above the clip picker, below the sample fields/summary; they are not in the
  sidebar. **Finish task** remains in the sidebar. Actions wrap when necessary;
  omit the Enter badge in narrow panels without changing its keyboard shortcut.
  Keep horizontal overflow local to long distance/reference strips.
- Put camera time and interval replay directly below the animal video, and sample
  progress in the selected individual's expanded card. Other cards show their
  first row. Highlight the active identity; metadata/actions stay attached to
  their own card. Keep **Finish task** prominent.
- The graph shows all individuals over elapsed time, highlighting the active
  track in orange. Use gaps for skipped/unreviewed distances, plus labeled status
  lanes: dotted not-visible, blocked absent, hollow unreviewed. Never imply zero
  meters for a visibility status. Use a compact graph (210 px for up to two
  individuals, 238 px for three), retaining at least 12 px per status lane as the
  count grows. Graph/keyboard/carrier explanations live in tooltips instead of
  permanent multi-line help blocks. Large individual counts or expanded inspection
  controls may still need scrolling; do not clip essential controls to force a fit.
- Species, camera, and Distance-completion filters plus selected task ID persist
  per browser/project, separately from ordinary Review. Clear stale filters and
  fall back to an available task if the saved selection disappears.
- **Left/Right:** scheduled samples. **Up/Down:** distance/visibility choices.
  **Enter:** open clock setup if alignment has not been saved; otherwise confirm
  the current sample. Clock-setup buttons show an **↵ Enter** badge in the expanded
  sidebar and compact rail. **J/K:** next/previous individual with wrapping.
  **A:** add an individual after alignment; it does not open clock setup.
  **P/N:** previous/next species task, including tasks sharing one video.
  **Tab / Shift+Tab:** next/previous reference clip with wrapping, retaining
  visibility or matching/nearest distance.
- Focused playback supports **Space**, **Shift+Left/Right** five-second inspection,
  **S/D** speed, **[/]** brightness, **{/}** contrast, **Z** reset zoom, **R** reset
  filters, **F/Escape** fullscreen. Clock-dialog arrows step frames and Enter
  confirms. Editable fields retain typing; dialog/busy guards prevent unrelated
  navigation or writes. Preserve English/French UI and shortcut help.

## Database Schema

Published migrations 1–22 remain unchanged. Migration 23 creates the original
Distance schema and adds `tags.individual_eligible`. Migration 24 introduces
`distance_review_tasks`, task-owned individuals, and task/annotator progress. It
discards legacy Distance annotations while preserving reference calibration/media
and ordinary Review data. Fresh installs and upgrades enforce the same task schema.
Distance remains unreleased; automatic preservation of legacy annotation evidence
is intentionally not required. Intermediate development schemas have no maintained
rebuild tool.

| Table | Purpose and key fields |
|---|---|
| `distance_imports` | Immutable original package, manifest, content hash and import time; unique project/content hash. |
| `reference_videos` | UUID, project, camera label, normalized path; unique project/path. |
| `reference_frames` | UUID, source reference, integer offset, optional precise playback offset, known meters; unique source/offset. |
| `distance_individuals` | UUID, task, video, task-local number, fixed target species, age/sex, first-seen offset and optional complete box, attribution and derived completion; unique task/number. |
| `distance_samples` | UUID, individual/offset, pending/annotated/skipped state, original AI suggestion, separate human reference/distance or skip, optional carrier, attribution; unique individual/offset. |
| `distance_individual_tags` | Individual/tag pair with attribution/time. |
| `distance_sample_behaviors` | Sample/behavior pair. |
| `distance_review_tasks` | UUID, video/target species (unique), original source count, task alignment, current completion/absence, completion history, and explicit AI rejection provenance. |
| `distance_review_progress` | Active individual per task/annotator, with update time. |

AI provenance is part of the same initial Distance schema: `distance_imports`
retains immutable package provenance (manifest, content hash and original payload),
with provenance on ordinary references, individuals and samples.
Imported individuals are ordinary `distance_individuals`; predictions become
`distance_samples` with `status = 'pending'`. Their immutable `ai_original` values
are separate from nullable human answer/attribution fields and survive confirmation
or correction. Pending rows never count toward completion, progress, or reviewed
graph distances. Identifying boxes come from the first usable mask; no mask means no
invented box or first-sighting evidence. Missing predictions produce explicitly
inferred Absent suggestions, with no human answer fields or review credit.

The initial schema includes precise identity playback positions, imported sample
times, review start attribution, and the pending-state constraint. Prepared references are
added/reused without overwriting conflicting calibration. Missing species must be
added/mapped before import. Content-identical packages are always no-ops; changed
content is a new import even with the same source/run IDs. Import and confirmed
replacement of untouched drafts are transactional. Viewing saved packages is
inspection only, not run switching.

`individual_observations` remains ordinary Review's independent store. Reuse
species, behaviors, tags, and annotator vocabulary; retain separate junctions.
The Injured/Disease built-ins and legacy custom tags become individually eligible
on v22 upgrade. New custom tags default to video-only and can be enabled in
Settings. Saved ineligible tags remain visible/removable but cannot be newly
assigned. Vocabulary CSVs preserve eligibility; legacy CSV imports do not clear it.

Sample invariants:

- Missing row at a scheduled offset means unreviewed.
- `annotated` requires a reference and finite positive copied meters, no skip
  reason. Preserve both reference provenance and its copied value until reconfirmed.
- `skipped` requires `individual absent` or `midpoint not visible`, no reference
  or meters, and may retain a carried-infant link. It counts as reviewed, but is
  not an exported observation.
- Integer `offset_ms` identifies the scheduled playback offset; recorded camera
  time is distinct from labeling time. Precise playback positions are not IDs.
- Foreign keys protect selected reference evidence. Junction pairs are composite
  primary keys. Box geometry is all-null or complete/valid; first-seen offsets
  must be inside the video. Parent/infant writes and reopening remain atomic.

## Exported Dataset (Deferred)

The research CSV will emit one row per **annotated** Distance sample, not ordinary
Review observations. Include species, age, sex, individual number/ID, exact
meters, sample behaviors, individual tags, annotator, folder/location/video, and
sample camera timestamp (year/month/day/hour/minute/second), explicitly unknown
when Simplified camera time has not been explicitly confirmed. Skip rows are omitted.

A separate transfer bundle must preserve skips as well as identities, anchors,
attributes, behavior/tag vocabulary, reference provenance/media-path resolution,
verified timing, completion, and resume state. Ordinary annotation CSV is not a
full-fidelity Distance transfer or database backup.

## Remaining Work

- Research CSV and transfer-bundle design/implementation.
- AI assistance: reference-mask overlays remain deferred. Imported animal masks are
  shown on their exact prediction frames with a visibility toggle; they hide during
  playback, seeking and box drawing. Selected masks are yellow and other animals gray.
  Mask rendering and inferred absences never change original AI values or human answers.
- Reference maintenance beyond package import: correct distances, relink media,
  safely delete unused references, and preserve evidence for already-used frames.
- Final individual-tag vocabulary, including any distinct skin-disease label.
- Real-footage validation of timestamp accuracy, multiple individuals, carried
  infants, and the latest UI/state refactor. Automated NiceGUI/Node coverage and
  earlier browser checks do not establish that this final workflow was validated
  end to end. Keep validation evidence in the cleanup execution record.

Duplicate-individual copying and model-assisted reference suggestions are outside
current scope. Revisit only with an explicit workflow/policy decision.
