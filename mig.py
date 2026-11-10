"""
NE Career Hub — Taxonomy Gap Finder
-------------------------------------
Reads jobs from a JSON file, loads master_degrees + master_specializations
(specializations are scoped to a degree_id) from Supabase, and asks Gemini to
check whether each (degree, specialization) pair mentioned in the eligibility
text already exists in the DB.

Does NOT publish anything. Does NOT insert anything by itself — read-only
against Supabase. Output: a human-readable report + a review-before-running
SQL insert file.

KEY RULES:
- Specializations are per-degree (e.g. "Agriculture" under B.Sc. is a
  different row than "Agriculture" under M.Sc.). A match must be on the
  (degree, specialization) PAIR, not the specialization name alone.
- Degree/qualification level matching is assumed reliable (per prior
  confirmation) — the AI should pick the degree from the EXISTING degrees
  list, not invent one. Only specialization existence is in question.
- Educational subject (e.g. "B.Sc in Agriculture") is a SPECIALIZATION check.
  Work experience domain (e.g. "3 years experience in Agricultural Extension")
  is a FUNCTIONAL AREA check. Never mix the two.
"""

import os
import json
import re
import requests
import logging
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

GOOGLE_MODEL   = os.environ.get("GOOGLE_MODEL", "gemini-1.5-flash")
GOOGLE_API_KEY = os.environ["GOOGLE_API_KEY"]
SUPABASE_URL   = os.environ["SUPABASE_URL"]
SUPABASE_KEY   = os.environ["SUPABASE_KEY"]

JOBS_FILE   = "old_jobs.json"
REPORT_FILE = "missing_taxonomy.txt"
SQL_FILE    = "missing_taxonomy_insert.sql"

# ─────────────────────────────────────────────
#  PROMPT
# ─────────────────────────────────────────────
def AUDIT_PROMPT(title: str, description: str, degrees: list, specializations_with_degree: list, functional_areas: list) -> str:
    return f"""You are auditing a job description against an existing database taxonomy.
You are NOT migrating or publishing anything. Only checking what already exists vs what is missing.

JOB DATA:
- Title: {title}
- Description: {description}

EXISTING DEGREES IN DB:
{json.dumps(degrees)}

EXISTING (DEGREE, SPECIALIZATION) PAIRS IN DB:
Specializations are scoped to a specific degree. The same subject can exist
under one degree but not another (e.g. "Agriculture" may exist under "B.Sc."
but not yet under "M.Sc."). Each entry below is a pair that already exists:
{json.dumps(specializations_with_degree)}

EXISTING FUNCTIONAL AREAS IN DB:
{json.dumps(functional_areas)}

RULES:

1. SPECIALIZATION = the academic subject of a degree. In Indian usage a
   qualification is written as "[Degree] in [Subject]" — e.g. "B.Sc in
   Agriculture", "Doctoral degree in Fisheries". If the eligibility text lists
   multiple subjects joined by "/" or "or" (e.g. "Agriculture/Animal
   Science/Fisheries/Community Science/Horticulture/Sericulture"), treat EACH
   one as a SEPARATE (degree, specialization) pair to check individually. All
   of them typically share the SAME degree in that sentence — apply it to each.

2. DEGREE MATCHING: pick the degree from the EXISTING DEGREES list above —
   do not invent a new degree name. If the JD says "Doctoral degree", match it
   to whatever the closest existing degree entry is (e.g. "Ph.D."). If the JD
   says "Bachelor's degree in Agriculture" / "B.Sc in Agriculture", match to
   the existing "B.Sc." (or equivalent) degree entry.

3. A (degree, specialization) pair EXISTS only if that EXACT pair is present
   in the EXISTING (DEGREE, SPECIALIZATION) PAIRS list — matching the
   specialization name alone under a DIFFERENT degree does NOT count as
   existing. E.g. if "Agriculture" exists under "B.Sc." but the JD requires
   "M.Sc. in Agriculture", that pair is MISSING even though "Agriculture" as a
   word exists elsewhere in the table.

4. FUNCTIONAL AREA = the professional work-experience domain, e.g. "3 years
   experience in Agricultural Extension Work". This has NOTHING to do with the
   subject of a degree. If the eligibility text doesn't mention required work
   experience in a specific domain, do not extract any functional areas.
   NEVER classify an educational subject as a functional area.

5. For each extracted (degree, specialization) pair or functional area, check
   CONFIDENTLY against the lists above (e.g. "Fisheries" matching "Fisheries
   Science" counts as confident). Do not force a fuzzy match to an unrelated
   existing value just because nothing better exists.

6. If it exists, mark "exists": true and note the matched DB values.
   If it does NOT exist, mark "exists": false and give:
   - "degree_name": the EXISTING degree name (from the degrees list) this
     specialization should be attached to.
   - "suggested_specialization_name": a properly formatted OFFICIAL canonical
     name (e.g. "Fisheries" -> "Fisheries Science", "Sericulture" ->
     "Sericulture"). Use standard Indian academic naming conventions for
     government job postings.

Respond ONLY with raw JSON, no markdown, in this exact shape:
{{
  "job_title": "{title}",
  "specializations_checked": [
    {{"extracted_text": "...", "degree_name": "...", "exists": true, "matched_specialization": "..." }},
    {{"extracted_text": "...", "degree_name": "...", "exists": false, "suggested_specialization_name": "..." }}
  ],
  "functional_areas_checked": [
    {{"extracted_text": "...", "exists": true, "matched_value": "..." }},
    {{"extracted_text": "...", "exists": false, "suggested_name": "..." }}
  ]
}}"""

# ─────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────
def strip_fences(text):
    return re.sub(r"```(json)?\n?", "", text).replace("```", "").strip()

def call_gemini(prompt: str) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GOOGLE_MODEL}:generateContent?key={GOOGLE_API_KEY}"
    resp = requests.post(
        url,
        headers={"Content-Type": "application/json"},
        json={
            "systemInstruction": {"parts": [{"text": "You are a strict, read-only data auditor. Output ONLY valid JSON."}]},
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.1, "maxOutputTokens": 4096},
        },
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()["candidates"][0]["content"]["parts"][0]["text"]

def fetch_degrees() -> list:
    """Returns list of {id, name}."""
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/master_degrees?select=id,name",
        headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    )
    r.raise_for_status()
    return r.json()

def fetch_specializations() -> list:
    """Returns list of {id, name, degree_id}."""
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/master_specializations?select=id,name,degree_id",
        headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    )
    r.raise_for_status()
    return r.json()

def fetch_functional_areas() -> list:
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/master_functional_areas?select=id,name",
        headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    )
    r.raise_for_status()
    return [row["name"] for row in r.json()]

def sql_escape(value: str) -> str:
    return value.replace("'", "''")

def write_text_report(missing_specializations: dict, missing_functional_areas: dict):
    lines = []
    lines.append("MISSING SPECIALIZATIONS (grouped by degree)")
    lines.append("=" * 50)
    if missing_specializations:
        for (degree_name, spec_name), titles in missing_specializations.items():
            lines.append(f"\n[{degree_name}] {spec_name}")
            for t in titles:
                lines.append(f"  - {t}")
    else:
        lines.append("(none)")

    lines.append("\n\nMISSING FUNCTIONAL AREAS")
    lines.append("=" * 50)
    if missing_functional_areas:
        for name, titles in missing_functional_areas.items():
            lines.append(f"\n{name}")
            for t in titles:
                lines.append(f"  - {t}")
    else:
        lines.append("(none)")

    with open(REPORT_FILE, "w") as f:
        f.write("\n".join(lines) + "\n")

def write_sql_inserts(missing_specializations: dict, missing_functional_areas: dict, degree_id_by_name: dict):
    lines = []
    lines.append("-- Auto-generated by audit_taxonomy.py")
    lines.append("-- Review names before running. Duplicates are possible if the AI phrased")
    lines.append("-- something slightly differently across jobs than what's already in the DB.")
    lines.append("")

    if missing_specializations:
        lines.append("-- master_specializations (name, degree_id)")
        for (degree_name, spec_name), titles in missing_specializations.items():
            degree_id = degree_id_by_name.get(degree_name)
            if degree_id is None:
                lines.append(
                    f"-- ⚠️ SKIPPED: could not resolve degree_id for degree '{degree_name}' "
                    f"(specialization '{spec_name}') — check spelling against master_degrees, add manually."
                )
                continue
            lines.append(
                f"INSERT INTO master_specializations (name, degree_id) "
                f"VALUES ('{sql_escape(spec_name)}', {degree_id}); "
                f"-- degree: {degree_name}"
            )
        lines.append("")

    if missing_functional_areas:
        lines.append("-- master_functional_areas")
        for name in missing_functional_areas.keys():
            lines.append(f"INSERT INTO master_functional_areas (name) VALUES ('{sql_escape(name)}');")
        lines.append("")

    with open(SQL_FILE, "w") as f:
        f.write("\n".join(lines) + "\n")

# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────
if __name__ == "__main__":
    if not os.path.exists(JOBS_FILE):
        logger.error(f"{JOBS_FILE} not found!")
        exit(1)

    with open(JOBS_FILE, "r") as f:
        jobs = json.load(f)

    logger.info("Fetching existing taxonomy from DB (read-only)...")
    degrees_raw = fetch_degrees()                     # [{id, name}, ...]
    specializations_raw = fetch_specializations()      # [{id, name, degree_id}, ...]
    functional_areas = fetch_functional_areas()        # [name, ...]

    degree_name_by_id = {d["id"]: d["name"] for d in degrees_raw}
    degree_id_by_name = {d["name"]: d["id"] for d in degrees_raw}
    degrees = [d["name"] for d in degrees_raw]

    # Build (degree, specialization) pairs for the prompt context
    specializations_with_degree = [
        {"specialization": s["name"], "degree": degree_name_by_id.get(s["degree_id"], "UNKNOWN")}
        for s in specializations_raw
    ]

    logger.info(
        f"  {len(degrees)} degrees, {len(specializations_with_degree)} (degree, specialization) "
        f"pairs, {len(functional_areas)} functional areas currently in DB."
    )

    missing_specializations = {}   # (degree_name, spec_name) -> [job titles]
    missing_functional_areas = {}  # spec_name -> [job titles]

    for idx, job in enumerate(jobs):
        title = job.get("title", "Unknown Title")
        logger.info(f"[{idx+1}/{len(jobs)}] Checking: {title}")

        prompt = AUDIT_PROMPT(title, job.get("description", ""), degrees, specializations_with_degree, functional_areas)
        try:
            raw = call_gemini(prompt)
            result = json.loads(strip_fences(raw))
        except Exception as e:
            logger.error(f"  ❌ Audit failed for {title}: {e}")
            continue

        for item in result.get("specializations_checked", []):
            if not item.get("exists"):
                degree_name = item.get("degree_name")
                spec_name = item.get("suggested_specialization_name")
                if degree_name and spec_name:
                    key = (degree_name, spec_name)
                    missing_specializations.setdefault(key, []).append(title)

        for item in result.get("functional_areas_checked", []):
            if not item.get("exists"):
                name = item.get("suggested_name")
                if name:
                    missing_functional_areas.setdefault(name, []).append(title)

    write_text_report(missing_specializations, missing_functional_areas)
    write_sql_inserts(missing_specializations, missing_functional_areas, degree_id_by_name)

    logger.info(f"\n🔍 Audit complete.")
    logger.info(f"  Missing (degree, specialization) pairs: {len(missing_specializations)}")
    logger.info(f"  Missing functional areas: {len(missing_functional_areas)}")
    logger.info(f"  Report written to {REPORT_FILE}")
    logger.info(f"  SQL insert script written to {SQL_FILE} (review before running!)")