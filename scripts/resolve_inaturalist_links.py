"""One-time dev tool: populate the `inaturalist` column of species.csv.

Queries the public iNaturalist API by scientific name and writes the canonical
taxon URL (https://www.inaturalist.org/taxa/<id>) into each row. Only confident
matches are written; placeholders, "... sp." morphotypes and synthetic detector
classes never match and stay blank. Idempotent — rows already filled are skipped.

Run from the repo root:

    uv run python scripts/resolve_inaturalist_links.py

Then review the printed summary and commit the updated CSV. The shipped app never
calls the network; it only reads the committed column.
"""

from __future__ import annotations

import csv
import json
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

CSV_PATH = Path(__file__).resolve().parent.parent / "review_app" / "data" / "species.csv"
API_URL = "https://api.inaturalist.org/v1/taxa?q={q}&per_page=5"
TAXON_URL = "https://www.inaturalist.org/taxa/{id}"
USER_AGENT = "review-app species link resolver (https://github.com/wild-chimpanzee-foundation/review-app)"
# iNaturalist asks for <= 1 request/second.
REQUEST_DELAY_SEC = 1.0
# Ranks we accept as a real, linkable taxon.
ACCEPTED_RANKS = {"species", "subspecies", "genus", "variety", "form", "hybrid"}


def resolve_taxon_url(scientific_name: str) -> tuple[str, str] | None:
    """Resolve a scientific name to (taxon_url, inat_name), or None if no confident match.

    Accepts a result when its name matches the query exactly, or when the search
    returned a single result of an accepted rank (an unambiguous synonym — e.g.
    iNaturalist files "Cephalophus ogilbyi brookei" under "Cephalophorus brookei").
    """
    url = API_URL.format(q=quote_plus(scientific_name))
    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (URLError, TimeoutError, json.JSONDecodeError, OSError) as e:
        print(f"  ! request failed for {scientific_name!r}: {e}", file=sys.stderr)
        return None

    results = [
        r
        for r in data.get("results", [])
        if r.get("id") and (r.get("rank") or "").strip().lower() in ACCEPTED_RANKS
    ]
    target = scientific_name.strip().lower()

    # 1. Exact name match.
    for r in results:
        if (r.get("name") or "").strip().lower() == target:
            return TAXON_URL.format(id=r["id"]), r["name"]
    # 2. Single unambiguous result (synonym / reclassification). Never for "... sp."
    #    morphotypes — those must not collapse onto one arbitrary species.
    is_morphotype = any(tok.lower() == "sp." for tok in scientific_name.split())
    if len(results) == 1 and not is_morphotype:
        r = results[0]
        return TAXON_URL.format(id=r["id"]), r["name"]
    return None


def main() -> int:
    with CSV_PATH.open(newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f, delimiter=";"))
    if not rows:
        print("species.csv is empty", file=sys.stderr)
        return 1

    header = rows[0]
    try:
        sci_idx = header.index("scientific_name")
    except ValueError:
        print("species.csv has no scientific_name column", file=sys.stderr)
        return 1

    if "inaturalist" not in header:
        header.append("inaturalist")
    inat_idx = header.index("inaturalist")

    matched = skipped = unmatched = synonym = 0
    synonyms: list[tuple[str, str, str]] = []
    for row in rows[1:]:
        # Leave blank/separator lines untouched so the file format is preserved.
        sci = (row[sci_idx] if sci_idx < len(row) else "").strip()
        if not sci:
            continue
        while len(row) <= inat_idx:
            row.append("")
        if row[inat_idx].strip():
            skipped += 1
            continue

        result = resolve_taxon_url(sci)
        time.sleep(REQUEST_DELAY_SEC)
        if result:
            link, inat_name = result
            row[inat_idx] = link
            matched += 1
            if inat_name.strip().lower() != sci.lower():
                synonym += 1
                synonyms.append((sci, inat_name, link))
                print(f"  ~ {sci} -> {link}  (iNat: {inat_name})")
            else:
                print(f"  ✓ {sci} -> {link}")
        else:
            unmatched += 1
            print(f"  · {sci} (no confident match)")

    with CSV_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter=";", lineterminator="\n")
        writer.writerows(rows)

    if synonyms:
        print("\nSynonym/reclassification matches — please verify these:")
        for sci, inat_name, link in synonyms:
            print(f"  {sci}  ->  {inat_name}  {link}")

    print(
        f"\nDone. matched={matched} (of which synonym={synonym})  "
        f"unmatched={unmatched}  already-filled={skipped}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
