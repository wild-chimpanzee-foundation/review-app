# Distance Annotation Feedback

**Status:** Feedback and decisions with implementation progress noted below.
Items not marked implemented remain planned or open. The
[current workflow](./reference-based-distance-annotation.md) describes shipped
behavior.

## 1. Independent, species-based annotator packages

**Feedback:** Distance annotator packages are independent of species review and
should be distributed by species.

**Confirmed:** Distance review is organized into species-specific tasks, one
canonical task per video and target species. A video containing chimps and duikers
appears twice in the Distance queue. Each task permits annotation only of its
target species and owns its individuals, samples, alignment, resume state, and
completion. The two tasks may use different alignment anchors and finish
independently. Packages distribute tasks while preserving their stable identities.

**Confirmed label source:** Create tasks from manual/confirmed Species Review
species and counts, not unconfirmed model predictions. No additional ordinary
Review completion flag or ordinary assignment is required once those labels exist.
Snapshot the source species/count context when generating packages.

**Confirmed absence outcome:** Provide an explicit **No target species visible**
action. It completes an empty task without a placeholder animal and retains author,
time, and a discrepancy against the manual source labels. Existing human target-
species individuals block absence; later additions invalidate it. Normal Finish
requires at least one individual and complete evidence. Correcting Species Review
or creating a task for another species happens separately, never automatically.

**Return reconciliation:** Accept a task return only when its evidence and required
alignment/reference/relationship dependencies can be applied consistently. On a
conflict, the importer chooses for that task whether to overwrite source changes
with the returned task or skip importing it. Overwrite applies the task and its
completion claim atomically; skip leaves source evidence unchanged and does not
store the skipped return in the app. Other safe species tasks for the same video
may still import. Preserve accepted submitted completion as provenance separately
from current source completion. Provide an import summary showing the choice and
record the decision with author/time. Users must retain the original import package
if they want to inspect or retry skipped tasks later; the app does not archive it.
No pending-return list or later-resolution workflow is required for the first release.
Richer resolution UI follows later. Independent duplicate reviews of one species are
not required, but a source task and its distributed copy may diverge.

**Implemented locally:** Species-specific tasks, task-scoped alignment, annotation,
resume/completion, and explicit absence with pending-AI rejection. The queue uses
confirmed Species Review labels and keeps different species tasks independent.

**Still open:** Package generation, supervisor package UI, and safe handoff/return.
Treat Distance
annotation as unreleased: no live databases use it. The developer's existing
annotations are disposable or can be migrated manually; automatic legacy annotation
migration is not required. Preserve existing reference frames and their
calibration/media. See the [work-package implementation plan](./distance-work-packages-plan.md)
and [Review issues design](./review-issues.md).

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
The simplified workflow has been tested and works well enough to use as the
default. Further real-footage timing evaluation is not an outstanding prerequisite.
Keep the Exact implementation for now as a code-configured alternative.

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

Automated timing, backend, UI, and migration tests cover these paths. Simplified
is the accepted default; retain Exact in the code for now.

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
open for correction. In species-specific tasks, check the task's target-species individuals;
other species tasks have independent completion.

**Implemented:** Finish video checks every individual in the current video after
sample coverage validation. Missing age/sex blocks completion and reports the
individual numbers and missing fields; the video remains open. Explicit **Unknown**
satisfies either field. Creation and in-progress editing still allow unset values;
clearing a field reopens video completion without changing sample coverage.
Schema v26 also reopens previously completed videos with unset attributes.
Task-scoped validation will follow the task and packaging work in item 1.

## 9. Detachable panels — dropped

**Decision:** Do not implement detachable animal/reference panels. This request
is removed from scope, not deferred.

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

When the sidebar is collapsed, the remaining content should expand into the
available space rather than leave empty reserved columns.

**Scope:** A general Distance UI design overhaul, not just isolated spacing
adjustments. Further visual redesign beyond the implemented changes below is
deferred.

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
counts and expanded inspection controls may still require scrolling. Further
visual redesign is deferred; detachable panels are dropped.

## Discussion progress

All ten feedback items are captured. Key clarifications:
- Mixed-species videos have separate target-species-only Distance tasks, with
  independent alignment, evidence, and completion. Packages distribute these tasks
  using manual/confirmed Species Review species and counts. No target species
  visible is an explicit completion outcome. Conflicting task returns are overwritten
  atomically or skipped; skipped payloads are not retained in the app. Accepted
  submitted completion is distinct from current source completion.
- Simplified is the frontend default, using an even frame near the midpoint and
  optional time confirmation. Exact remains only as a code-configured workflow.
- The first-sample lead-in plays from the video opening.
- Visible samples require at least one behavior, with **Unidentified** used for
  zero-behavior selections; multiple behaviors remain allowed. Carry confirmed
  behaviors forward. Absent and Not visible automatically use no behavior.
- The graph previews unsaved distance choices.
- Detachable comparison panels are dropped, not deferred.
- The right-hand sidebar is collapsible/restorable and initial layout improvements
  are implemented. Further visual redesign is deferred.

Open implementation/policy questions remain noted in the relevant sections.

**First quick-win batch completed:** First-sample playback, a collapsible right
sidebar, 24-hour timestamp entry, and an initial spacing reduction. Other feedback
items are not implemented by this batch.
