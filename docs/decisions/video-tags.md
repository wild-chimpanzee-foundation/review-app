# Video Tag System

## Context

Three separate feature requests were consolidated into a single per-video tagging concept:
- "Fire / nice video / other" highlight flags
- "Broken metadata" flag for videos where ffprobe returned incomplete or corrupt data
- General video-level labels that sit outside the species/behavior annotation system

## Decision

Introduce a `Tag` reference table and a `VideoTag` join table, modelled after the existing `Behavior` system but without species association.

### Schema

**`Tag`**
```
id          UUID PK
key         String UNIQUE   — e.g. "fire", "nice_shot", "broken_metadata"
name_en     String
name_fr     String | None
color       String | None   — Quasar color name, e.g. "red", "amber"
icon        String | None   — Material icon name, e.g. "local_fire_department"
is_custom   Boolean
```

**`VideoTag`**
```
video_id    FK → videos.video_id   PK
tag_id      FK → tags.id           PK
tagged_by   String | None
tagged_at   DateTime
```

### Seeded built-in tags

| key | icon | color |
|-----|------|-------|
| `fire` | `local_fire_department` | `deep-orange` |
| `nice_shot` | `star` | `amber` |
| `broken_metadata` | `report_problem` | `red` |

### `broken_metadata` automation

`sync_videos()` already sets `validation_error` on `Video` when ffprobe fails. After sync, auto-apply the `broken_metadata` tag to any video where `validation_error IS NOT NULL`, so it appears in the tag filter without manual annotator work.

### Review UI

A row of toggle chips above the species cards in the annotation panel — one chip per tag, using the tag's icon and color. Clicking toggles the tag on/off for the current video, persisted immediately (independent of the Submit button).

### Queue filter

New "Tags" multi-select in the filter drawer, same pattern as the existing species/behavior filters:
```sql
EXISTS (SELECT 1 FROM video_tags WHERE video_id = v.video_id AND tag_id IN (...))
```

### Export

New `tags` column in the CSV export: comma-separated tag keys, e.g. `"fire,nice_shot"`.

## Open question

Should custom tags be creatable inline from the review screen ("+  Add tag" chip), or only from a settings/setup page? Inline is faster for annotators but adds UI complexity to an already busy panel.
