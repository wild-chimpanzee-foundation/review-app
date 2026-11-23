# Database, backups, restore, and migrations

Review App uses a local SQLite database and exchanges assignment packages between
installations. Database files commonly reach 500 MB–1 GB. Keep SQLite and the
existing migration runner for this workflow. Keep gzip level 1 for retained
backups until measurements justify a change. The checklist below tracks the work
needed to make recovery predictable as databases grow.

## Storage and durability

Keep the live SQLite database on local storage. At current sizes, measure
annotation save latency, bulk-import write contention, and recovery time to
decide when the architecture needs to change. Revisit a client/server database
if multiple machines need to edit the same live project or sustained write
contention persists after reducing write transaction duration.
[SQLite deployment guidance](https://www.sqlite.org/whentouse.html)

The app uses WAL with `synchronous=NORMAL`. A power loss or OS crash can
roll back recent commits under this configuration. Benchmark `FULL` for
annotation saves and bulk imports before choosing a durability setting.
[Connection settings](../../review_app/backend/provider/local_data_provider.py) ·
[SQLite synchronous modes](https://www.sqlite.org/pragma.html#pragma_synchronous)

Measure tables and indexes with `dbstat`, reusable pages with
`freelist_count`, and the WAL separately. Measure how much space is taken by
embedded assignment ZIPs. Return processing reads those archives, so moving
them outside SQLite would require backing up and restoring the database and
archive files together.
[SQLite dbstat](https://sqlite.org/dbstat.html) ·
[Assignment archive storage](../../review_app/backend/db/models.py) ·
[Assignment return processing](../../review_app/backend/annotation_package/handoff.py)

## Backup and restore

The app creates SQLite snapshots with `VACUUM INTO`, streams gzip compression,
and retains recent, daily, weekly, and monthly copies in its local data folder.
Compare the SQLite Online Backup API for routine snapshots with the current
compacted snapshot. Benchmark plain SQLite, gzip level 1, and zstd on the same
snapshots. Record snapshot and compression times separately, archive size,
restore time, peak scratch space, and impact on annotation saves.
[Backup implementation](../../review_app/backend/db/backup.py) ·
[SQLite Online Backup API](https://sqlite.org/backup.html) ·
[VACUUM INTO](https://sqlite.org/lang_vacuum.html)

The current restore can decompress a gzip file separately for integrity
checking, schema inspection, and staging. The Settings fallback can copy gzip
bytes directly over the live database. Use one recovery path for Settings and
onboarding.
[Restore implementation](../../review_app/backend/db/backup.py) ·
[Settings database management](../../review_app/app/pages/settings/database.py)

1. Stage the candidate once on the live database's filesystem. Check that it
   belongs to Review App and has a supported schema. Run SQLite integrity and
   foreign-key checks and validate the final candidate after any migrations.
2. Before cutover, stop writers, drain database connections, and preserve a
   durable, uncompressed copy of the current database. Account for committed
   changes in the WAL when preserving it.
3. Replace the database atomically and reopen it through the app. Keep the
   rollback copy until reopen succeeds. If reopen fails, reinstate the old copy
   through the same recovery path. On startup, recover an interrupted cutover
   before opening the database normally.

Publish backups only when complete. A failed compression job should leave its
completed SQLite snapshot usable. Local copies cover mistakes such as a bad
import. Recovery from device loss requires a verified copy on another device or
a managed backup destination. Decide how much work can be lost and how long
recovery can take, then use those targets to set backup frequency and retention.
Include reconnecting footage in a fresh-machine restore drill.
[Data and backup locations](../../README.md#data-storage)

## Schema migrations

Keep the versioned SQL and Python migrations. The runner explicitly starts a
transaction for SQLite DDL, validates foreign keys when enforcement was enabled,
and rejects databases newer than the app. The repo tests rollback and compares
schema created from scratch with schema upgraded through migrations. Preserve
the published migration history and legacy fixtures.
[Migration runner](../../review_app/backend/db/migrations.py) ·
[Migration contributor rules](../../CONTRIBUTING.md#database-migrations) ·
[Schema parity tests](../../tests/test_schema_parity.py)

Separate initialization of a genuinely new database from upgrades to an existing
one. Today the provider calls `create_all()` before running migrations, and a
missing version row is treated as fresh. Open existing databases through a
version check before creating tables. Recognize supported legacy schemas
explicitly and reject unknown schemas before writing. Restore older backups by
running the same upgrader against the staged candidate before cutover.
[Provider startup](../../review_app/backend/provider/local_data_provider.py) ·
[Version stamping](../../review_app/backend/db/migrations.py)

Revisit Alembic if revision collisions or repetitive schema changes become a
regular cost. A verified baseline would preserve the existing upgrade path,
with Alembic managing subsequent revisions. Its revision graph, schema diffing,
and SQLite batch operations would help author migrations. Data preservation
rules, such as keeping Distance measurements linked to their original reference
frames, still need migration logic and upgrade tests.
[Alembic autogenerate](https://alembic.sqlalchemy.org/en/latest/autogenerate.html) ·
[Alembic SQLite batch operations](https://alembic.sqlalchemy.org/en/latest/batch.html)

## Work checklist

- [x] Unify staged restore and rollback (`0c3edf7`). Settings uses the staged
  restore through the local provider. Automated tests cover invalid files,
  foreign-key violations, a staging disk failure, and recovery of original
  records after a failed or interrupted reopen.
- [x] Separate fresh initialization from existing upgrades and reuse the
  upgrader for restore (`0c3edf7`). Tests cover rollback after an injected
  migration failure and upgrading stamped and unstamped v3 backups while
  preserving annotations and vocabulary.
- [ ] Verify fresh/upgraded schema parity and preservation of reference and
  assignment records across supported upgrades.
- [ ] Time a verified restore of a representative 1 GB backup on an annotator
  machine. Record peak disk use and simulate a cutover failure. Time a
  table-rebuild upgrade on a copy of the same database.
- [ ] Define acceptable data loss and recovery time. Verify recovery from a
  separate backup location on a fresh machine, including footage reconnection.
- [ ] Profile representative 500 MB and 1 GB databases. Compare snapshot and
  compression options, measure growth by table and index, and benchmark WAL
  durability settings before changing the defaults.

Related data-integrity findings are tracked in the
[code quality and data integrity audit](to-dos.md#code-quality-and-data-integrity-audit).
