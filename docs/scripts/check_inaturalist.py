"""
Fetch iNaturalist data for all species in species.csv.
Saves results to review_app/data/species_inat.csv.

Per species: 2 list calls (locale=en, locale=fr) + 1 detail call for summary.
~134 species × 3 calls = ~400 calls total; 0.8s sleep between species keeps
us well under the 100 req/min unauthenticated limit.
"""
import csv
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

CSV_IN  = Path("review_app/data/species.csv")
CSV_OUT = Path("review_app/data/species_inat.csv")
BASE    = "https://api.inaturalist.org/v1/taxa"
SLEEP   = 0.8

FIELDNAMES = [
    "scientific_name",
    "inat_id",
    "inat_name",        # canonical name on iNaturalist (may differ from our key)
    "common_name_en",
    "common_name_fr",
    "photo_url",
    "wikipedia_url_en",
    "wikipedia_url_fr",
    "summary_en",
    "match_status",     # ok / bad_match / no_result / skipped
]


def fetch(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=15) as r:
        return json.loads(r.read())


def query_taxa(name: str, locale: str) -> list:
    params = urllib.parse.urlencode({
        "q": name,
        "is_active": "true",
        "order": "desc",
        "order_by": "observations_count",
        "locale": locale,
        "per_page": 1,
    })
    return fetch(f"{BASE}?{params}").get("results", [])


def genus_of(name: str) -> str:
    return name.split()[0].lower()


def is_placeholder(name: str) -> bool:
    return "sp." in name or " " not in name


with open(CSV_IN, newline="", encoding="utf-8") as f:
    rows = list(csv.DictReader(f, delimiter=";"))

print(f"Processing {len(rows)} species → {CSV_OUT}\n")

with open(CSV_OUT, "w", newline="", encoding="utf-8") as out_f:
    writer = csv.DictWriter(out_f, fieldnames=FIELDNAMES)
    writer.writeheader()

    counts = {"ok": 0, "bad_match": 0, "no_result": 0, "skipped": 0}

    for i, row in enumerate(rows[10:30]):
        name = row["scientific_name"].strip()
        base_row = {f: "" for f in FIELDNAMES}
        base_row["scientific_name"] = name

        if is_placeholder(name):
            base_row["match_status"] = "skipped"
            counts["skipped"] += 1
            writer.writerow(base_row)
            print(f"[{i+1:3}/{len(rows)}] SKIP  {name}")
            continue

        # --- EN call ---
        results_en = query_taxa(name, "en")
        if not results_en:
            base_row["match_status"] = "no_result"
            counts["no_result"] += 1
            writer.writerow(base_row)
            print(f"[{i+1:3}/{len(rows)}] NONE  {name}")
            time.sleep(SLEEP)
            continue

        t = results_en[0]
        if genus_of(t["name"]) != genus_of(name):
            base_row["inat_name"] = t["name"]
            base_row["match_status"] = "bad_match"
            counts["bad_match"] += 1
            writer.writerow(base_row)
            print(f"[{i+1:3}/{len(rows)}] BAD   {name} → {t['name']} ({t.get('preferred_common_name','')})")
            time.sleep(SLEEP)
            continue

        inat_id   = t["id"]
        inat_name = t["name"]
        common_en = t.get("preferred_common_name", "")
        photo_url = (t.get("default_photo") or {}).get("medium_url", "")
        wiki_en   = t.get("wikipedia_url", "")
        wiki_fr   = wiki_en.replace("en.wikipedia.org", "fr.wikipedia.org") if wiki_en else ""

        # --- FR call (common name only) ---
        results_fr = query_taxa(name, "fr")
        common_fr = ""
        if results_fr and genus_of(results_fr[0]["name"]) == genus_of(name):
            common_fr = results_fr[0].get("preferred_common_name", "")

        # --- Detail call (summary) ---
        detail  = fetch(f"{BASE}/{inat_id}")
        summary = detail.get("results", [{}])[0].get("wikipedia_summary", "")
        # Strip HTML tags for plain text storage
        import re
        summary = re.sub(r"<[^>]+>", "", summary).strip()

        writer.writerow({
            "scientific_name": name,
            "inat_id":         inat_id,
            "inat_name":       inat_name,
            "common_name_en":  common_en,
            "common_name_fr":  common_fr,
            "photo_url":       photo_url,
            "wikipedia_url_en": wiki_en,
            "wikipedia_url_fr": wiki_fr,
            "summary_en":      summary,
            "match_status":    "ok",
        })
        counts["ok"] += 1
        print(f"[{i+1:3}/{len(rows)}] OK    {name} → {inat_name}  en='{common_en}'  fr='{common_fr}'  summary={'Y' if summary else 'N'}")

        time.sleep(SLEEP)

print(f"""
=== DONE ===
OK:         {counts['ok']}
Bad match:  {counts['bad_match']}
No result:  {counts['no_result']}
Skipped:    {counts['skipped']}
Output:     {CSV_OUT}
""")
