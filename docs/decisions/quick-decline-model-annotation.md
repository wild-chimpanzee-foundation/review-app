# Quick accept/decline for model-proposed annotations

## Problem

When scanning videos filtered by a model-proposed species (e.g. all "chimpanzee" detections), the user needs two fast actions:

- **Accept** — model is right; add the proposed annotation (equivalent to pressing Enter)
- **Decline** — model is wrong, but the video is not blank; leave for full annotation later

## Decision

Add a `skipped_species` boolean column to `VideoLabel`.

| State | `is_blank` | `skipped_species` |
|---|---|---|
| Truly empty video | `True` | — |
| Model right, fully annotated | `False` | `False` / NULL |
| Model wrong, needs real annotation later | `False` | `True` |

## Rejected alternatives

- **Empty `IndividualObservation`** — ambiguous with blank video
- **Sentinel `labeled_by` value** — a hack, breaks filtering

## Implementation sketch

1. Add `skipped_species = Column(Boolean, nullable=True)` to `VideoLabel` in `models.py`
2. Add migration in `migrations.py`
3. Add a "Decline" button in `review_app/app/pages/review.py` alongside the existing submit buttons
