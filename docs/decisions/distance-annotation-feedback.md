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

**To clarify:** What supplies the species labels used to build packages without
requiring ordinary species review to be complete?

## 2. Use a 24-hour timestamp

**Requested change:** Display and enter camera times using a 24-hour clock
(`HH:mm:ss`), without AM/PM.

**Implemented:** The alignment/correction dialog now uses an explicit
`YYYY-MM-DD HH:mm:ss` masked text input instead of a browser-localized date/time
control. Existing timestamp displays already use 24-hour time. This is a
formatting change, not a timezone conversion.

## 3. Simplify initial time alignment

**Feedback:** Ask only whether the first frame shows an even or odd second;
selecting the exact transition frame is unnecessary.

**Current workflow:** Locate an exact odd-to-even transition frame, enter its
camera timestamp, and specify whether the opening frame is even.

**Confirmed direction:** Keep both approaches available for testing; do not
choose a permanent replacement yet:
- **Exact alignment:** Keep the current transition-frame workflow.
- **Simplified alignment:** Select opening-frame parity. An even opening samples
  at playback 0, 2, 4… seconds; an odd opening samples at 1, 3, 5… seconds.

**Important trade-off:** Parity alone does not establish where the next camera
second begins within playback. The simplified schedule can be offset from exact
camera-even-second transitions by a subsecond amount. Absolute camera date/time
also needs a source if it remains required for export.

**Evaluation goal:** Compare annotation effort and timing accuracy on real clips
before choosing a default or removing either approach.

**To decide:** How to expose the two modes for testing, what happens to the
existing timestamp input, and how mode changes affect already annotated videos.
Testing both must not silently reinterpret existing saved evidence.

## 4. Play up to the first sample

**Requested change:** Make **Play to this sample** available on the first scheduled
sample when that sample is not the video's opening frame.

**Confirmed:** Play from the start of the video to that sample, then pause.
“First frame” means the video's opening frame, not the individual's first-seen
frame. If the sample is already the opening frame, no lead-in is needed.

**Implemented:** The first nonzero sample supports replay from playback zero.
Later samples retain replay from the previous scheduled sample.

## 5. Exactly one mandatory behavior

**Confirmed:**
- Visible distance samples require exactly one behavior.
- Choose the first behavior manually; there is no fixed initial default.
- Subsequent samples preselect the preceding confirmed behavior.
- **Absent** and **Not visible** allow no behavior and automatically select the
  no-behavior state, rather than requiring a manual behavior choice.

Prefilling should remain a draft until confirmation, rather than saving evidence
merely because a sample was opened.

**To decide:** How to handle existing visible samples with zero or multiple
behaviors.

## 6. Live graph preview

**Requested change:** Update the graph immediately when the current selection
changes, without waiting for sample confirmation.

**Confirmed:** Preview the unsaved distance/visibility choice at the current
sample. Leaving without confirmation discards the preview and restores the saved
graph state.

**Presentation recommendation:** Distinguish the preview from saved evidence.

## 7. Add an unknown age option

**Requested change:** Add **Unknown** to the age dropdown, alongside Adult,
Adolescent, Juvenile, and Infant.

This should represent an explicit assessment, distinct from an unset age.

## 8. Require age and sex when finishing

**Requested change:** Allow individual creation without age or sex, but require
both before **Finish video** succeeds.

**Working interpretation:** Explicit **Unknown** satisfies the requirement;
unset values do not. Identify individuals with missing fields and keep the video
open for correction. In species-specific packages, check individuals of the
assigned species, consistent with species-scoped completion.

**Dependency:** Add the unknown age option first.

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

The right-hand annotation sidebar must be collapsible and restorable. Collapsing
it should give the comparison panels the freed width without losing the active
individual, sample, or unsaved form selection. Keep a visible way to reopen it.

Coordinate this layout work with detachable panels: when a panel is popped out
or the sidebar is collapsed, the remaining content should expand into the
available space rather than leave empty reserved columns.

**Scope:** A general Distance UI design overhaul, not just isolated spacing
adjustments. Detailed visual design remains to be worked out.

**Partially implemented:** Added an annotation-panel icon toggle in the header,
next to the filters, that collapses/restores the right drawer without rebuilding
players or form controls. It uses the same round white icon-button styling as the
filter toggle. Restore the header first if it has been hidden.
An initial spacing pass reduces sidebar/card padding and panel gaps and removes
the workspace width cap. The follow-up removes comparison-card borders, shadows,
backgrounds, and padding entirely, along with outer workspace padding and the
video height cap. Videos fill their column at their native aspect ratio without
cropping or stretching. The full redesign and detachable panels remain deferred.

## Discussion progress

All ten feedback items are captured. Key clarifications:
- Mixed-species videos go to both relevant species packages; annotation and
  completion are scoped to the assigned species.
- Keep exact and simplified alignment testable; the final choice is deferred.
- The first-sample lead-in plays from the video opening.
- Choose the first visible behavior manually, then carry it forward. Absent and
  Not visible automatically use no behavior.
- The graph previews unsaved distance choices.
- Pop out either full comparison panel independently, or both, with the remaining
  panel expanding in the original tab.
- Overhaul the Distance layout to reduce wasted space and make the right-hand
  annotation sidebar collapsible/restorable.

Open implementation/policy questions remain noted in the relevant sections.

**First quick-win batch completed:** First-sample playback, a collapsible right
sidebar, 24-hour timestamp entry, and an initial spacing reduction. Other feedback
items are not implemented by this batch.
