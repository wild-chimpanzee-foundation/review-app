# Distance branch cleanup plan

Status: local code/database cutover completed with explicit approval. The local
`feature/reference-based-distance-annotation` and `refactor/distance-cleanup-final`
contain the compact five-commit replacement, assembled from the unchanged `main`
base. The live database is now rebuilt v23: ordinary Review preserved exactly,
Distance reset. Original code/database and the untracked original cleanup note
are archived outside the worktree. The remote branch is unchanged: **do not pull
its old history into the rewritten local branch**. Publishing requires separate
approval and an explicit verified force-with-lease.

## Execution progress

- [x] Archive original HEAD and verify a Git bundle containing unpushed commits.
- [x] Make an SQLite backup with successful integrity/foreign-key checks.
- [x] Consolidate migrations 23–29 into final migration 23; preserve published
  entries/helpers 1–22 (verified structurally against `main`). Keep the v22 fixture.
- [x] Reject newer databases before startup DDL, journal-mode changes, or seeding.
- [x] Implement and test the read-only-by-default converter, including exact
  ordinary-record preservation and cleanup after failure.
- [x] Convert a real backup into a separate v23 preview: retained all ordinary
  values, including 2,140 videos and 2,029 observations. Reset 24 Distance
  individuals, 580 samples, and 46 prepared reference frames. Not installed.
- [x] Fix root-aware reference discovery and manual/bulk sample validation; remove
  both deployment-identity xfail markers. Cover most-specific roots, relocation,
  and ambiguous external-reference handling.
- [x] Extract camera-layout maintenance from queue/reference loading; call it
  explicitly during Distance page setup and Refresh references.
- [x] Split backend reference, identity, clock/review and transactional sample
  responsibilities behind the provider facade. Compute bulk schedule/completion
  once; enforce finite-positive reference distances on manual and bulk writes.
- [x] Extract typed selection/draft ownership, concrete card/reference/sample
  rendering, clock/preparation/anchor dialogs, and CSS from the page.
- [x] Consolidate workflow documentation and retire the historical UI handoff.
- [x] Run full-suite, focused integration, independent review, and isolated
  real-browser validation; fix reference-refresh and pause-remount regressions.
- [x] Assemble the compact local replacement history from the unchanged main base,
  with feature tests alongside code and project tooling in a separate commit.
- [x] Validate two separate backup conversions before requesting cutover approval.
- [x] Obtain explicit **local-only, no-push** approval; take another fresh stopped-app
  backup, rebuild/verify the replacement, preserve the original untracked note,
  move the local feature branch, and atomically install v23 with rollback recovery.
- [x] Verify installed provider startup, integrity/FKs, all 17 retained tables by
  exact value fingerprints, and all eight Distance tables empty. Retained totals:
  2,140 videos and 2,029 ordinary observations. Leave the application stopped.
- [ ] Publish the rewritten remote branch only after separate explicit approval.
  Reverify its current tip and use that exact tip as the force-with-lease value.

### Validation and architecture outcome

- Initial schema/backup/converter/repository/timing/deployment checkpoint:
  **458 passed**. Final integrated CI-equivalent check: **1,293 passed,
  4 deselected**, Ruff lint and format clean. The final code tree is reused for
  history reconstruction; verify tree identity rather than rerunning identical
  broad suites solely because commit IDs changed.
- Independent checkpoint and architecture reviews: **OK**. The architecture
  reviewer found disabled empty-list reference discovery; fixed and reviewed
  again. Parent integration also restored the reference controls' refreshable
  slot and eliminated duplicate rounding of the proposed clock timestamp.
- Chromium at 1280×800 passed **20** synthetic-media workflow checks: resume,
  reference switching/visibility, independent reference preparation, fixed-frame
  redraw cancellation, J/K identification frames, unsnapped inspection and manual
  pause snapping, suffix cancellation/overwrite/final sample, persistent carried
  infant/separation, reload, completion warnings/advance, clock alignment, and
  retained ordinary annotations/playable ordinary Review.
- The browser found that dialog-triggered NiceGUI remounts discarded the manual
  pause listener. The Distance hook now follows replacement elements like the
  existing shared controls/anchor editor; a Node regression and independent
  follow-up review cover installation, suppression, detachment, and cleanup.
- Final Distance browser console: no errors. Ordinary Review rendered and its
  stored annotations remained identical, but its console logged a Quasar missing
  tooltip anchor and an undefined `__scrollAnnotationCardIntoView` initialization
  call. The relevant ordinary Review source was not changed by this cleanup;
  these console issues are recorded, not investigated or fixed here.
- Browser footage was synthetic timestamped 25-fps media, **not** biological or
  camera-timestamp ground truth. NiceGUI user tests and Node tests supplement,
  rather than replace, this bounded browser evidence. Focused backend-only pytest
  invocations still report the existing unknown `main_file` configuration warning.
- The page remains about 2,089 lines because cross-workflow persistence, playback,
  queue navigation, completion and media lifetime remain together. Extracted
  collaborators own concrete rendering/dialog/state responsibilities. The provider
  facade is about 439 lines, with explicit-session domain helpers. A new backend
  assignment wrapper was not introduced: both public commands retain their
  existing keyword API and share the same transactional writer and validation.
  Further page/controller or application-wide provider redesign is deferred.
- Recovery evidence is stored outside the repository under the dated
  `review-app-cleanup/20261015-115757` user-data directory: verified Git bundle,
  original/stopped SQLite backups, two conversion reports/previews, lane patches,
  exact-head CI logs, and browser manifest/screenshots/validation report. Both real
  conversions preserved all retained values, including 2,140 videos and 2,029
  ordinary observations, while intentionally resetting all eight Distance tables.
  The approved installed cutover is recorded in `cutover-20261015-113915`: fresh
  v29 backup, untouched retired live database, rebuilt v23 copy, conversion
  fingerprints, preserved untracked note, and post-install verification.

### Distance quick-win follow-up validation

- Focused drawer, clock-correction, timing/player, keyboard, and reference-dimming
  tests: **217 passed**. Translation coverage: **10 passed** (the isolated
  translation run retains the existing unknown `main_file` warning). Ruff lint,
  formatting, and whitespace checks pass.
- Isolated Chromium composition harness at 1280×800, using synthetic 25-fps media:
  collapsing the 400 px right drawer expands the workspace from 580 to 980 px and
  each comparison panel from 278 to 478 px, without horizontal overflow. Reopening
  restores the geometry, player element IDs, and unsaved input.
- First-sample replay from playback zero pauses at **1.00001 s** for a 1 s sample.
  The real masked clock input displays `2026-10-12 16:02:50` as text and accepts/
  saves `2026-10-12 23:02:50` without an AM/PM control or timezone conversion.
- Frameless-video follow-up: **60 passed** across drawer/reference-dimming tests.
  Isolated Chromium checks with the application theme at 1280×800 and 1920×1080
  confirm zero comparison-card padding/borders/shadows, transparent backgrounds,
  and full-width uncapped videos. At 1920 px with both drawers hidden, each 16:9
  video occupies 958×538.875 px. Native 4:3 sizing, dark-theme overrides, and
  fullscreen/dialog sizing also pass; unrelated cards retain their borders.
- Header-toggle follow-up: **64 passed** across drawer, translation, and ordinary
  UI smoke tests. The annotation toggle is now a round white header icon next to
  filters, not a workspace action. Isolated Chromium at 1280×800 confirms header
  placement, collapse/reopen (580→980→580 px workspace), header hide/restore,
  preserved player IDs/draft input, and no horizontal overflow.
- Below-video control-layout follow-up: **160 passed** across drawer, graph
  geometry, reference dimming, selection/draft position, bulk-apply UI, and infant
  creation tests. Ruff lint/format and whitespace checks pass.
- Isolated Chromium using the real Distance rendering components, three animals,
  fourteen distances, two reference clips, a selected behavior, and a carried
  infant fits without page scrolling at 1920×1080 (drawers closed) and 1280×800
  (both drawers open). The wide reference controls occupy about 192 px vertically;
  the graph plus its heading occupies 262 px. Videos remain 958×538.875 px at the
  wide size. Fields share a row when wide and wrap when narrow; toggling the infant
  field and resizing preserves behavior/infant selection. No horizontal page
  overflow; long option strips retain local horizontal scrolling.
- Moving Confirm/advance and Apply to end above the reference clip picker was
  followed by **five focused tests** for placement, confirmation, bulk cancellation,
  and sidebar completion; the earlier broad checks were not repeated. The same
  bounded Chromium layout check still fits without page scrolling at both sizes.
  With the added action row, reference controls occupy about 232 px when wide,
  and the narrow layout ends at about 790 px in an 800 px viewport. Finish stays
  in the sidebar; no save/completion semantics changed.
- Compact-sidebar follow-up: **seven focused tests passed** for mini/expanded
  state preservation (including an empty queue), rail Add/select/finish controls,
  completion/busy guards, ordinary selected-card behavior, and translation parity.
  The rail uses separate entry markers so J/K still enumerates one full card per
  individual, and completion indicators remain derived/read-only.
- Isolated Chromium at 1920×1080 confirms 400→72→400 px right-drawer widths
  (workspace 1520→1848→1520 px with filters closed), visible rail actions,
  preserved player IDs and draft input, header hide/restore, and no horizontal
  overflow. No broad suite was rerun for the compact-sidebar change.
- This is bounded UI/media validation, not a full application/real-footage review.
  No live application database was used or changed.

### Converter operation

Run from the cleanup worktree against a backup, not the live database:

```sh
uv run python -m scripts.rebuild_distance_dev_db --source original-v29.db --output rebuilt-v23.db
# Review the retained fingerprints and discarded row counts, then create a new file:
uv run python -m scripts.rebuild_distance_dev_db --source original-v29.db --output rebuilt-v23.db --execute
```

Output must not already exist. Source/output cannot be the same file. The command
never installs its output. In multi-root projects, external references must have
an unambiguous registered-root path; equal camera labels alone no longer match.
Single-root explicit external reference assignments remain supported.

## Decisions

- Preserve ordinary Review data; discard/rebuild Distance annotations and prepared
  reference frames. Keep a complete backup so discarded development work remains
  recoverable with the original code.
- Focused refactor, not an application-wide redesign. Preserve the implemented
  Distance workflow and fix the known cross-import-root reference collision.
- Rewrite only this private feature branch, never `main`.
- Keep `main`'s migrations 1–22 unchanged; consolidate branch migrations 23–29
  into one final migration 23.

## Inspection findings

At inspected HEAD `05d7709`, the branch forks directly from `main` at `1af75ea`.

- 76 commits beyond `main`, 50 on the first-parent path, including repeated
  patch/merge history. Local HEAD is 15 commits ahead of the tracked branch.
- Net change: 59 files, 15,426 additions and 198 deletions. Of the additions,
  6,130 are application code and 8,214 are tests. Squashing history alone will
  not reduce the implementation; tests are not the primary cleanup target.
- `review_app/app/pages/distance_review/page.py`: 2,744 lines combining rendering,
  session/draft state, navigation, media management, four dialog workflows,
  persistence calls, timing, and completion.
- `review_app/backend/provider/distance_repository.py`: 1,199 lines combining
  discovery, queue queries, identity edits, clock verification, sample writes,
  carried-infant rules, and completion.
- `LocalDataProvider` creates missing tables before running migrations. Fresh
  databases skip migration SQL and are stamped current. Both fresh construction
  and the actual upgrade path must remain covered.
- The committed schema fixture is version 22: an excellent upgrade baseline.
- The runner currently accepts a higher stored version and overwrites its stamp
  with `len(MIGRATIONS)`. A current v29 database must not be opened normally by
  the rewritten v23 application before conversion.
- Two strict expected failures in `tests/test_distance_deployment_identity.py`
  document reference discovery and sample acceptance mixing deployments when
  separate project import roots both contain a camera named `cam_1`.
- `get_distance_video_queue()` and reference discovery perform camera-layout
  repair during loading; query and maintenance responsibilities are intertwined.
- Bulk assignment reuses the transactional single-sample writer, which is good,
  but repeats schedule validation and completion scans for each offset.
- Media behavior already has useful boundaries in `player.py`, JavaScript files,
  `tracks.py`, and `backend/distance_timing.py`. Retain these rather than rebuild.

Focused inspection validation: `uv run pytest -q tests/test_schema_parity.py
 tests/test_migrations.py tests/test_distance_deployment_identity.py` produced
**23 passed, 2 xfailed**. Pytest also reported the existing unknown `main_file`
configuration warning. No full-suite or browser validation was run for this plan.

## Execution order

### 1. Establish recovery points before changing anything

1. Record HEAD, merge base, working-tree status, tracked remote tip, and database
   path/version. Confirm `main` has not advanced; recalculate the migration
   boundary if it has. Do not overwrite unrelated working-tree changes.
2. Create an archive branch/tag at the original HEAD and a Git bundle outside
   the working tree, including all currently unpushed commits.
3. Stop the application before database conversion. Use SQLite's backup API to
   capture a consistent complete database; do not copy only a live WAL-mode `.db`.
   Record its schema version and integrity checks.
4. Develop cleanup on a separate branch/worktree. Isolate its database as well:
   a Git worktree still shares the default platform user-data directory.
   Do not launch the refactored application against the original database.

Deliverable: independently restorable original code and database.

### 2. Consolidate the unpublished schema

Files: `review_app/backend/db/migrations.py`,
`review_app/backend/provider/local_data_provider.py`, migration/schema tests.

- Replace migrations 23–29 with a final migration 23 containing all eight Distance
  tables, final box/sample checks, completion/progress fields, clock-anchor
  fields, precise reference playback offsets, and tag eligibility/backfill.
- Remove branch-only legacy transitions: point-marker cleanup, repeated Distance
  table rebuilds, and compatibility migrations between unpublished Distance
  schemas. Preserve any still-needed ordinary-data backfill.
- Keep migration 23's schema definitions frozen and explicit. Do not implement
  a historical upgrade by referencing whatever ORM metadata exists in the future.
- Preserve migrations 1–22 and the version-22 fixture. Do not regenerate that
  fixture to v23 and thereby lose the upgrade regression.
- Add a newer-schema rejection before provider `create_all()` or any other
  startup write; also protect direct calls to the migration runner. Reject v24+
  with actionable guidance rather than silently relabeling the database.
- Retain transaction/foreign-key safety required by historical migrations.
  Condensing Distance rebuilds is not a reason to weaken older upgrade behavior.
- Replace v25–29-specific tests with tests of supported entry points: fresh
  database, real v22 upgrade with ordinary data, repeated current startup,
  newer-version rejection without mutation, rollback, and final constraints.
- Compare CHECK constraints for all new constrained tables, not just sample
  columns/indexes. Fresh and upgraded schemas must enforce the same invariants.

Acceptance: both fresh install and v22 upgrade produce final v23 with ordinary
records/IDs unchanged. No automatic migration from the old private v23–29 chain
is promised. Its version numbers now describe a different history.

### 3. Build a one-off development database converter

Proposed file: `scripts/rebuild_distance_dev_db.py`; focused converter tests.

This is a local maintenance command, not an application-startup migration.

- Require explicit source and output paths. Default to dry-run/reporting;
  never overwrite the source or an existing output.
- Validate the actual source version and expected schema. Initially support the
  current branch schema needed for this database, not every historical variant.
  Fail on unknown tables/columns instead of silently ignoring possible data.
- Create a new database with the final target schema, without provider startup
  seeding. Copy retained tables in foreign-key dependency order, preserving exact
  keys, annotation UUIDs, values, attribution, timestamps, and relationships.
- Preserve projects/directories, videos and media metadata/paths, ordinary labels,
  individual observations, observation behaviors, model predictions, assignments,
  annotators, species/collections/project vocabulary, tags/video tags, tag
  eligibility, custom/shared behaviors, and application settings.
- Do not copy the eight Distance tables: `reference_videos`, `reference_frames`,
  `distance_individuals`, `distance_samples`, `distance_individual_tags`,
  `distance_sample_behaviors`, `distance_video_reviews`,
  `distance_review_progress`. Report the discarded row counts explicitly.
- Keep reference *video files*, their imported `videos` rows, and video tags.
  They must remain discoverable; only their prepared frames and Distance review
  evidence are reset. Do not remove media or transcoding caches.
- Create the target's version stamp normally; never copy/decrement the source
  `_schema_version` stamp as a substitute for rebuilding the schema.
- Check retained rows by keys and values, not counts alone, plus
  `PRAGMA integrity_check` and `PRAGMA foreign_key_check`. Confirm all Distance
  tables are empty and ordinary export/queue behavior still works.
- After a successful report and explicit execution approval, install the new
  database with the app stopped. Handle stale WAL/SHM sidecars safely. Keep the
  original database and report outside the application data path.

Do not use the ordinary annotation CSV as the preservation mechanism: it is not
an exact backup of the full ordinary-review database.

### 4. Fix deployment identity as a separate correctness change

Files: `review_app/backend/camera_layout.py`, reference discovery/write validation,
project relocation/import hooks, `tests/test_distance_deployment_identity.py`.

- Separate human-readable `camera_id` from the full reference-matching identity.
  Use one resolver for the animal and reference source, including the stable
  `ProjectDir.id` and camera/deployment within that import root. Prefer the most
  specific containing root for nested imports; never use an absolute root path
  as durable identity.
- Keep existing displayed camera labels and ordinary Review IDs. No deployment
  table or ordinary annotation redesign is required for this fix.
- Apply the same scope to discovery and sample validation; a UI-only filter is
  insufficient. Resolve prepared/manual reference paths consistently and fail
  clearly when their deployment cannot be established, rather than falling back
  to equal camera labels. Retain supported explicit reference assignments.
- Preserve project-directory relocation behavior. Add only the relevant relocation
  and nested-root regression coverage needed by the chosen resolver.
- Extract legacy camera-layout maintenance from query construction. Keep repair
  an explicit, scoped import/refresh operation where needed for v22 data; loading
  a queue should not repeatedly scan and rewrite an entire project.
- Remove the two expected-failure markers when their assertions pass.

Acceptance: roots A and B with the same camera label cannot discover or accept
one another's reference frames. Same-root reference discovery still works.

### 5. Refactor Distance backend around existing transaction boundaries

Keep the current public `LocalDataProvider` API and mixin integration. Do not
replace the provider architecture throughout the app.

Suggested responsibility boundaries (names may be adjusted during implementation):

- `distance_references.py`: reference discovery/preparation, deployment scope,
  queue/species-context queries.
- `distance_review.py`: clock verification, resume/progress, completion policies.
- `distance_samples.py`: individual/sample commands and carried-infant mutation.
- `distance_repository.py`: small facade preserving public entry points; use
  concrete helpers with explicit sessions, not another web of cross-calling mixins.

The initial suggestion was a small typed assignment value shared by manual and
apply-to-end commands. The implemented boundary instead retains their stable
keyword API and centralizes validation/mutation in the shared session-aware
writer; the UI has an explicit frame-versus-visibility selection type. Keep
validation/domain helpers independent of NiceGUI. Do not convert every provider
dictionary or model in the repository to a new abstraction.

- Share command validation and mutation rules; remove accidental differences
  such as finite-distance checking depending on a bulk-only flag.
- Keep parent/infant writes, behavior replacement, creation, and review reopening
  atomic. Do not open nested sessions inside one bulk operation.
- Compute the bulk schedule once and refresh affected completion once at the end
  if this can be done without changing attribution/completion semantics. Validate
  with existing bulk and completion tests; no speculative performance framework.
- Preserve deliberate workflow differences between correcting one sample and
  repeating a choice through the end.
- Remove branch-only unused/compatibility APIs only after checking callers and
  moving their useful behavioral assertions to the supported commands.

### 6. Refactor the page by state and workflow, not arbitrary line chunks

Keep `page.py` as composition/root wiring and preserve its registered route.
Use concrete collaborators/render functions; avoid page-sized mixins or helpers
that depend on every page attribute.

Suggested boundaries within `review_app/app/pages/distance_review/`:

- `session.py`: typed selection/draft/assignment state and pure navigation rules.
- `individuals.py`: individual cards and creation/redraw dialogs.
- `references.py`: reference browsing and preparation dialog.
- `clock.py`: clock-alignment dialog.
- `sample_controls.py`: sample controls, apply-to-end and completion dialogs.
- `styles.css`: move the large static CSS block out of Python.
- Existing `player.py`, JavaScript modules, and `tracks.py`: retain their roles.

Important changes:

- Stop overloading `selected_frame_id` with both frame UUIDs and visibility text.
  Represent reference selection and absent/not-visible choices explicitly.
- Make the distinction between scheduled sample identity and inspected playback
  position explicit; preserve exact playback precision and integer sample IDs.
- Give drafts one owner and explicit precedence: saved evidence, existing draft,
  then carried-forward defaults. Reference browsing must not replace a draft.
- Capture video/individual/sample context for asynchronous actions and validate
  it before applying results. Keep existing busy/dialog/stale-selection guards
  and NiceGUI stable-client handling; these are correctness requirements.
- Centralize post-write refresh/draft invalidation for affected parent/infant
  samples, without refreshing/recreating video elements unnecessarily.
- Preserve ordinary Review defaults when using the shared player. Do not move
  all shared playback code or redesign its JavaScript lifecycle in this pass.

Target: composition/orchestration in the page and extracted modules with a single
understandable role, not an arbitrary line-count reduction. Move pure state assertions out of tests
that currently require extensive page-attribute setup where that helps; retain
actual NiceGUI and JavaScript integration coverage.

Behavior lock: first-seen individual switching, resume-at-unreviewed behavior,
manual/automatic pause snapping, unsnapped inspection/dialog players, draft
retention, visibility dimming, overlapping boxes, infant identity/inheritance,
transactional suffix overwrite, final-sample retention, and explicit completion
with species/count warnings and next-video navigation must remain unchanged.

### 7. Consolidate docs and reconstruct the final history last

- Reduce `reference-based-distance-annotation.md` to the current workflow,
  invariants, schema, and remaining work. Remove repeated implementation
  checklists, obsolete migration numbers, and superseded proposals.
- Archive or replace the outdated UI handoff with a link to the current design;
  keep one authoritative explanation of each behavior.
- Leave research export, transfer bundles, reference maintenance, recording-time
  correction, and further UI redesign out of this cleanup.
- Review `.agents/skills/parallel-work/SKILL.md` and `.pi/settings.json`
  independently. Keep useful project tooling separate from the feature commits;
  do not silently drop it or mix it into architecture changes.
- Do not laboriously rebase 76 historical commits or resolve obsolete merge
  conflicts. Build a new branch from the agreed `main` base using the validated
  final tree and stage coherent slices.
- Aim for roughly 4–6 meaningful commits: final schema/backend; deployment-scope
  fix; Distance UI/state; integration/shared-workflow protections; docs and
  optional tooling. Keep relevant tests with their implementation. Adjust the
  split to dependencies; a smaller number of honest commits is better than
  artificially bisecting inseparable changes.
- Review the final diff against the archived original tree. Every behavior/code
  difference must be attributable to planned cleanup or the explicit bug fix.
- Local reconstruction uses five commits: schema/scoped backend and integrations;
  safe development converter; UI/state/shared-player protections; authoritative
  documentation; and separate project tooling. Match its final tree against the
  validated cleanup tree before accepting the replacement.
- After approval, update the remote private branch using an explicit
  `--force-with-lease` tied to a freshly verified remote tip. Never force-push
  `main`; retain the archive until the new branch and converted database work.

History cleanup comes last so normal cleanup commits remain available for
review/debugging until the replacement is validated.

## Validation gates

Run the smallest affected tests and Ruff checks per step. Do not rerun a complete
baseline in every step. At integration, run `make ci` once on the reconstructed
branch. Keep the existing version-22 upgrade fixture.

Focused groups:

- Schema/converter: migration, schema-parity, retained-data converter, and backup
  version-rejection tests. Test the converter against a backup copy before install.
- Deployment: deployment identity, camera layout, reference preparation,
  directory relocation, and repository sample-validation regressions.
- Backend commands: repository, completion, timing, first-seen absence, and
  apply-to-end tests.
- UI/state: drawer, workflow, keyboard, selection-position, queue-state,
  reference-dimming, anchor-editor, and apply-to-end UI tests. Node-backed tests
  remain useful but do not establish real browser/media correctness.
- Ordinary Review integrations: species/tag vocabulary, annotator deletion,
  video reprobe/completion, shared player and UI smoke tests.

Before calling the cleanup complete, manually exercise a representative
animal/reference deployment in a real browser: clock alignment, two individuals,
carried infant then separation, reference switching, both visibility choices,
manual pause/inspection, apply-to-end overwrite, reload, and video completion.
Check ordinary Review and its retained annotations after conversion as well.

## Completion criteria

- Only unpublished migration history is condensed; v22 ordinary databases still
  upgrade and fresh databases have identical final constraints.
- Your ordinary Review records are preserved exactly and the old database remains
  recoverable; Distance data loss is explicit and limited to the agreed reset.
- The two cross-root identity regressions pass normally.
- State, rendering, reference work, and transactional sample mutation have clear
  ownership without an application-wide framework rewrite.
- The final feature diff is easier to understand, tests remain meaningful, and
  the remote branch has a compact, reviewable history with a recovery archive.
