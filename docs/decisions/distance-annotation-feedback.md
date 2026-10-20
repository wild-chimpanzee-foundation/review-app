# Distance Annotation Feedback

**Status:** Feedback and decisions with implementation progress noted below.
Items not marked implemented remain planned or open. The
[current workflow](./reference-based-distance-annotation.md) describes shipped
behavior.

## 1. Independent, species-based annotator packages

**Feedback:** Distance annotator packages are independent of species review and
should be distributed by species.

**Confirmed:** A mixed-species video is included in both relevant species
packages, rather than assigned exclusively to one. Each recipient annotates only
the species assigned to their package. Completion is scoped to that assigned
species, independently of the other species package.

**Confirmed label source:** Build packages from manual/confirmed species
annotations, not unconfirmed model predictions. Distance annotation and completion
remain independent of ordinary species-review completion once those labels exist.

**Still open:** Implement species-based package generation and species-scoped
annotation/completion.

## 2. Use a 24-hour timestamp

**Requested change:** Display and enter camera times using a 24-hour clock
(`HH:mm:ss`), without AM/PM.

**Implemented:** Both Exact and Simplified alignment use explicit 24-hour
`YYYY-MM-DD HH:mm:ss` fields with calendar and 24-hour time pickers, including
seconds, prefilled with the proposed/current expected time. Both have **−1 s /
+1 s** buttons that adjust the timestamp, including minute/date rollover, without
moving the selected video frame. In Simplified mode these buttons or picker edits
count as an explicit correction; an untouched automatic prefill remains estimated.
Native browser date/time inputs were replaced because their AM/PM presentation
depends on browser locale. Neither control converts the camera's timezone.

## 3. Simplify initial time alignment

**Feedback:** Ask only whether the first frame shows an even or odd second;
selecting the exact transition frame is unnecessary.

**Exact workflow:** Locate an exact odd-to-even transition frame, enter its
camera timestamp, and specify whether the opening frame is even.

**Revised direction after testing:** Simplified is the default annotator workflow;
Exact remains implemented but is available only through code configuration, not
a frontend selector. Replace parity-only setup for new Simplified alignments:
- **Exact alignment (code-only opt-in):** Keep the transition-frame workflow.
- **Simplified alignment:** Start near the video midpoint and select any frame
  whose visible camera timestamp shows an even second, preferably away from a
  timestamp change. An exact transition frame is not required. Sample every two
  seconds backwards and forwards from that selected frame.

**Important trade-off:** Parity alone does not establish where the next camera
second begins within playback. The simplified schedule can be offset from exact
camera-even-second transitions by a subsecond amount. Testing found odd displayed
seconds from the first sample on some clips. Reusing Exact's forward-frame estimate
could also push a late-even frame into the next odd second. The revised Simplified
grid preserves the selected frame's phase without this forward rounding/padding.
Irregular frames or camera-clock drift still require real-footage validation.

**Evaluation goal:** Validate annotation effort and timing accuracy on real clips;
the Exact implementation remains available for developer-configured comparison.

**Implemented:** Simplified opens at the midpoint (or its saved anchor), retains
inspection/frame-step controls, and uses **Frame shows an even camera second.**
as the confirmation action. Bold **Choose even frame** text and frame controls sit
immediately below the player, before timestamp fields. The extra confirmation
checkbox, long gray hint, and separate expected-time confirmation button are removed. There is no frontend mode selector or Exact calibration
option under the default configuration. The code-only
`DISTANCE_CLOCK_ALIGNMENT_MODE` constant in `review_app/app/config.py` defaults to
`"simplified"`; developers may set `"exact"` to use the retained Exact workflow.
Saved reviews are not reinterpreted when this default changes.

The dialog displays the expected timestamp for the current frame, using an
existing verified clock or file metadata as a suggestion. It updates while seeking.
This is explicitly labeled an estimate and is not silently saved as verified time.
Timestamp correction is optional: a 24-hour date/time field with pickers and seconds is
prefilled with the current expected time. Leave it unchanged to align frames only,
or edit it (including calendar/time picker and ±1 s changes) to establish camera
time. Pressing the even-frame button confirms the frame, not an untouched timestamp
estimate. Otherwise camera time remains unknown and sample labels show playback
offsets. Moving frames refreshes the estimate and discards the prior frame's optional
correction. If the frame changes during capture, ask the user to check and press again.

Existing Exact and parity-only reviews retain their original schedules and evidence.
Changing a saved mode or anchor, including converting an old parity-only review,
uses the explicit Distance reset option and destructive confirmation. Cancellation
or invalid input leaves evidence unchanged. Reconfirming an unchanged anchor or
adding/correcting its timestamp preserves evidence, progress, and completion.
A future export must handle unknown absolute camera timestamps.

Scheduled stops are now selectable immediately after alignment, before adding an
individual, so alignment can be checked using graph markers, arrow navigation,
and replay. These browse-only actions do not create annotation evidence or resume
records; saving still requires an individual.

Automated timing, backend, UI, and migration tests cover these paths. Comparing
effort and subsecond accuracy on real footage remains open. Simplified is now the
default; Exact has not been removed from the code.

## 4. Play up to the first sample

**Requested change:** Make **Play to this sample** available on the first scheduled
sample when that sample is not the video's opening frame.

**Confirmed:** Play from the start of the video to that sample, then pause.
“First frame” means the video's opening frame, not the individual's first-seen
frame. If the sample is already the opening frame, no lead-in is needed.

**Implemented:** The first nonzero sample supports replay from playback zero.
Later samples retain replay from the previous scheduled sample.

## 5. At least one behavior for visible samples

**Revised decision:** For now, require at least one behavior, not exactly one.
- Visible distance samples with no selected behavior use **Unidentified**.
- Multiple selected behaviors remain allowed.
- Existing visible samples with zero behaviors should be set to **Unidentified**;
  existing multiple-behavior selections are retained.
- Subsequent samples preselect the preceding confirmed behaviors.
- **Absent** and **Not visible** allow no behavior and automatically select the
  no-behavior state, rather than requiring a manual behavior choice.

Prefilling should remain a draft until confirmation, rather than saving evidence
merely because a sample was opened.

**Implemented:** Confirming a visible sample with no behavior saves
**Unidentified**; multiple behaviors are retained. This applies to ordinary saves,
Apply to end, and carried-infant evidence. Absent/Not visible clears the draft
selection and disables its behavior field; backend writes also remove behaviors
for either visibility status. New stops prefill the preceding confirmed behaviors
without saving them until confirmation. The behavior selector explains the fallback.
Schema v27 backfills existing zero-behavior visible samples, retains multiple
visible behaviors, and removes behavior links from skips without changing sample
provenance, timing, progress, or completion.

## 6. Live graph preview

**Requested change:** Update the graph immediately when the current selection
changes, without waiting for sample confirmation.

**Confirmed:** Preview the unsaved distance/visibility choice at the current
sample. Leaving without confirmation discards the preview and restores the saved
graph state.

**Implemented:** Explicit distance/visibility changes (buttons, keyboard, and
reference switching) immediately project the current sample into the graph without
mutating saved samples. Distance previews use a cyan diamond; visibility previews
use cyan dashed intervals. A graph badge and tooltips label **Unsaved preview**.
Navigation/individual changes discard the preview, and confirmation replaces it
with ordinary saved evidence. Untouched prefills are not shown as confirmed data;
carried-infant distance locks remain respected.

## 7. Add an unknown age option

**Requested change:** Add **Unknown** to the age dropdown, alongside Adult,
Adolescent, Juvenile, and Infant.

**Implemented:** Both individual creation and sidebar editing offer **Unknown** age.
It is stored as an explicit assessment, distinct from an unset age. Schema v26
preserves existing identities, relationships, and samples while expanding the age
constraint; existing unset values are not silently changed to Unknown.

## 8. Require age and sex when finishing

**Requested change:** Allow individual creation without age or sex, but require
both before **Finish video** succeeds.

**Working interpretation:** Explicit **Unknown** satisfies the requirement;
unset values do not. Identify individuals with missing fields and keep the video
open for correction. In species-specific packages, check individuals of the
assigned species, consistent with species-scoped completion.

**Implemented:** Finish video checks every individual in the current video after
sample coverage validation. Missing age/sex blocks completion and reports the
individual numbers and missing fields; the video remains open. Explicit **Unknown**
satisfies either field. Creation and in-progress editing still allow unset values;
clearing a field reopens video completion without changing sample coverage.
Schema v26 also reopens previously completed videos with unset attributes.
Species-package-scoped validation will follow the packaging work in item 1.

## 9. Independently detachable animal and reference panels

**Clarified direction:** Pop out the full animal or reference panel, not merely
a read-only image mirror. Either panel can be detached independently, and both
can be detached if desired.

When one panel is detached, the remaining panel expands to use the full available
comparison width in the original tab. The detached panel retains its associated
controls and remains part of the same annotation session; popping it out must
not create a separate review or independent selection state.

**Feasibility:** Possible in principle, using user-triggered companion tabs or
windows with coordinated session state. Panel ownership, playback/selection
synchronization, and main-tab layout reflow need implementation validation.

**Still open:** Exact playback-sync guarantees and how closing a companion view
reattaches its panel. The desired interaction is established; these mechanics
have not yet been specified.

## 10. General design overhaul and collapsible annotation sidebar

**Requested change:** Rework the Distance annotation layout to reduce wasted
space, including excessive white borders, margins, padding, and gaps. Prioritize
usable animal/reference image area and essential annotation controls.

The right-hand annotation sidebar must collapse to a narrow rail, rather than
completely disappear. Keep **A / Add**, a completion checkbox indicator and number
per individual, and the green finish checkmark visible. Clicking an individual
selects it; completion remains derived from sample coverage. Collapsing gives the
comparison panels the freed width without losing the active individual, sample,
or unsaved form selection. Keep the expand/collapse button in the sidebar itself,
visible in both modes, independently of whether the page header is shown.

Coordinate this layout work with detachable panels: when a panel is popped out
or the sidebar is collapsed, the remaining content should expand into the
available space rather than leave empty reserved columns.

**Scope:** A general Distance UI design overhaul, not just isolated spacing
adjustments. Detailed visual design remains to be worked out.

**Partially implemented:** A sticky toggle at the top of the sidebar switches
between a 400 px drawer and a 72 px action rail without rebuilding players or full
form controls. It remains available in both modes with no header duplicate. The
rail retains Add, individual selection/completion marks, and guarded video
completion. In the expanded view, completed individuals have green numbers and
checked icons; their borders are green unless active, when orange is retained.
An initial spacing pass reduces sidebar/card padding and panel gaps and removes
the workspace width cap. The follow-up removes comparison-card borders, shadows,
backgrounds, and padding entirely, along with outer workspace padding and the
video height cap. Videos fill their column at their native aspect ratio without
cropping or stretching.

The below-video controls now use a compact distance/visibility toolbar, an
adaptive behavior/carrying/infant grid, inline saved/selected summaries, and a
smaller horizontal reference browser. Confirm/advance and Apply to end are in
the reference controls, immediately above the clip picker; Finish video stays in
the annotation sidebar. Graph height is reduced for typical
individual counts while retaining readable status lanes; long help text moves to
tooltips. At the validated desktop sizes the standard three-individual workflow
fits without page scrolling, without shrinking the videos. Very large individual
counts and expanded inspection controls may still require scrolling. Detachable
panels and any further visual redesign remain deferred.

## Discussion progress

All ten feedback items are captured. Key clarifications:
- Mixed-species videos go to both relevant species packages; annotation and
  completion are scoped to the assigned species. Package species labels come from
  manual/confirmed species annotations.
- Simplified is the frontend default, using an even frame near the midpoint and
  optional time confirmation. Exact remains only as a code-configured workflow.
- The first-sample lead-in plays from the video opening.
- Visible samples require at least one behavior, with **Unidentified** used for
  zero-behavior selections; multiple behaviors remain allowed. Carry confirmed
  behaviors forward. Absent and Not visible automatically use no behavior.
- The graph previews unsaved distance choices.
- Pop out either full comparison panel independently, or both, with the remaining
  panel expanding in the original tab.
- Overhaul the Distance layout to reduce wasted space and make the right-hand
  annotation sidebar collapsible/restorable.

Open implementation/policy questions remain noted in the relevant sections.

**First quick-win batch completed:** First-sample playback, a collapsible right
sidebar, 24-hour timestamp entry, and an initial spacing reduction. Other feedback
items are not implemented by this batch.
