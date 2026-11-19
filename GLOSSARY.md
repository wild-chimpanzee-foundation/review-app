# Annotation review

Terms for project footage, deployment setup, and delegated Species Review and
Distance Review work.

## Language

### Footage and deployments

**Physical camera**:
The camera device, which may be used at different locations or during different
recording periods. Its identity alone does not identify a deployment.

**Deployment**:
A particular camera placement and recording period to which ordinary footage and
its reference footage belong. A deployment belongs to a project and may include
footage from multiple import roots through explicit links, not matching names alone.
_Avoid_: Physical camera (when referring to a deployment)

**Import root**:
A registered footage directory belonging to a project, within which media paths
and folder-derived labels are interpreted. Relocating the directory does not make
it a new import root.

**Deployment assignment**:
The association of a video with a project deployment, established during import
or explicitly chosen during project administration. It is separate from delegating
review work to an annotator.

**Unresolved footage**:
Imported footage without a deployment assignment, requiring an administrator to
match it to a project deployment.

**Reference video**:
Footage used to calibrate distances for ordinary footage in the same deployment.
Being identified as reference footage does not by itself establish which
deployment it belongs to.

**Unmatched reference**:
A reference video whose assigned deployment has no registered ordinary footage.
This may indicate incomplete footage or an assignment needing attention, rather
than an error; it is distinct from footage with no deployment assignment.

### Review work and delegation

**Allocation**:
The choice of who reviews which work before assignments are prepared, independently
for Species Review and Distance Review. Distance Review work is grouped by target
species, with one recipient per selected species group.

**Assignment**:
A defined selection of videos delegated for one review stage; a Distance Review
assignment targets exactly one species. It defines the delegated work, not which
annotations the recipient is permitted to edit locally.
_Avoid_: Work package (when referring to the delegated work)

**Removed assignment**:
An assignment removed from delivery, without revoking its delegated work or the
ability to accept its returns.

**Assignment handoff**:
The workflow of preparing an assignment, opening it as a fresh annotator project,
returning progress, and accepting that progress in the originating project.

**Distance task**:
The Distance Review work for one video and one target species, with independent
annotation, alignment, progress, and completion.

**Return**:
A checkpoint of all saved state in the assignment scope, including partial,
untouched, and empty work. Returning work does not close the assignment or prevent
further annotation.
