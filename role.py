"""
NE Career Hub — role_name Backfill Script
Reads existing job + job_eligibility_criteria data (exported as JSON), uses Gemini
to infer a `role_name` per eligibility GROUP (a group_id = one complete AND-logic
eligibility path = one post/role), and outputs SQL UPDATE statements instead of
writing to the DB directly — review the .sql file before running it.

Input JSON shape (one row per job, mirrors a Supabase REST embed query):
[
  {
    "id": 123,
    "title": "...",
    "description": "...",
    "job_eligibility_criteria": [
      {
        "id": 4501,
        "group_id": 1,
        "qualification_level_id": 3,
        "degree_id": 12,
        "specialization_id": 45,
        "functional_area_id": null,
        "exp_years_required": 2
      },
      ...
    ]
  },
  ...
]

Get this export with:
  jobs?select=id,title,description,job_eligibility_criteria(*)
"""

import os
import json
import time
import re
import requests
import logging
from collections import defaultdict
from dotenv import load_dotenv

load_dotenv()

# ─────────────────────────────────────────────
#  LOGGING & CONFIG
# ─────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

GOOGLE_MODEL   = os.environ.get("GOOGLE_MODEL", "gemini-1.5-flash")
GOOGLE_API_KEY = os.environ["GOOGLE_API_KEY"]
SUPABASE_URL   = os.environ["SUPABASE_URL"]
SUPABASE_KEY   = os.environ["SUPABASE_KEY"]

INPUT_FILE  = "jobs_eligibility_export.json"
OUTPUT_SQL  = "backfill_role_name.sql"
OUTPUT_LOG  = "backfill_role_name_debug.json"


# ─────────────────────────────────────────────
#  MASTER TABLE LOOKUPS (id -> name, for readable prompts)
# ─────────────────────────────────────────────
def fetch_master_table(table_name: str) -> dict:
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/{table_name}?select=id,name",
        headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
    )
    r.raise_for_status()
    return {row["id"]: row["name"] for row in r.json()}

def load_all_masters() -> dict:
    logger.info("Fetching master tables for readable prompts...")
    return {
        "qualification_levels": fetch_master_table("master_qualification_levels"),
        "degrees": fetch_master_table("master_degrees"),
        "specializations": fetch_master_table("master_specializations"),
        "functional_areas": fetch_master_table("master_functional_areas"),
    }

def describe_requirement(req: dict, masters: dict) -> str:
    ql = masters["qualification_levels"].get(req.get("qualification_level_id"), "Unspecified level")
    deg = masters["degrees"].get(req.get("degree_id"))
    spec = masters["specializations"].get(req.get("specialization_id"))
    fa = masters["functional_areas"].get(req.get("functional_area_id"))
    exp = req.get("exp_years_required") or 0

    parts = [ql]
    if deg:
        parts.append(deg)
    if spec:
        parts.append(f"in {spec}")
    if fa:
        parts.append(f"(experience area: {fa})")
    parts.append(f"— {exp} yr(s) experience required" if exp else "— fresher")
    return " ".join(parts)


# ─────────────────────────────────────────────
#  THE ROLE_NAME PROMPT
# ─────────────────────────────────────────────
def ROLE_NAME_PROMPT(title: str, description: str, groups_summary: list) -> str:
    groups_text = "\n\n".join(
        f"Group {g['group_id']}:\n" + "\n".join(f"  - {line}" for line in g["lines"])
        for g in groups_summary
    )
    return f"""You are labeling government job eligibility paths with the exact post/role name they qualify a candidate for.

JOB TITLE: {title}

JOB DESCRIPTION (HTML, may list one or multiple posts):
{description}

ELIGIBILITY GROUPS (each group_id is one complete, self-sufficient eligibility path — AND logic within a group; different group_ids are alternative paths, which may be for the SAME post or for DIFFERENT posts if this is a multi-post recruitment):
{groups_text}

RULES:
1. If the description advertises a SINGLE post, every group gets the SAME role_name — the exact post title as named in the source (e.g. "Junior Engineer", "Staff Nurse"). Do not invent a role_name that isn't grounded in the title/description text.
2. If the description advertises MULTIPLE posts (e.g. "Junior Engineer (Civil)" and "Junior Engineer (Mechanical)" as separate posts with separate vacancies), match each group to the post whose qualification requirements it corresponds to. Use the qualification branch/specialization in the group to disambiguate which post it belongs to.
3. role_name should be the post/designation name only — no department, no vacancy counts, no age/salary info.
4. If you genuinely cannot determine a specific post name from the text, output "Unspecified" rather than guessing.

Output ONLY raw JSON in this exact shape, one entry per group_id listed above:
{{
  "groups": [
    {{"group_id": 1, "role_name": "..."}}
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
            "systemInstruction": {"parts": [{"text": "You are a strict data labeler. Output ONLY valid JSON."}]},
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.1, "maxOutputTokens": 2048},
        },
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()["candidates"][0]["content"]["parts"][0]["text"]

def sql_escape(value: str) -> str:
    return value.replace("'", "''")

def group_criteria_by_group_id(criteria_rows: list) -> dict:
    groups = defaultdict(list)
    for row in criteria_rows:
        groups[row["group_id"]].append(row)
    return groups


# ─────────────────────────────────────────────
#  MAIN EXECUTION
# ─────────────────────────────────────────────
if __name__ == "__main__":
    if not os.path.exists(INPUT_FILE):
        logger.error(f"{INPUT_FILE} not found! Export it from Supabase first, e.g.:\n"
                     f"  jobs?select=id,title,description,job_eligibility_criteria(*)")
        exit(1)

    with open(INPUT_FILE, "r") as f:
        jobs = json.load(f)

    masters = load_all_masters()

    sql_statements = []
    debug_output = []

    for idx, job in enumerate(jobs):
        job_id = job.get("id")
        title = job.get("title", "Unknown Title")
        description = job.get("description", "")
        criteria_rows = job.get("job_eligibility_criteria", [])

        if not criteria_rows:
            logger.info(f"[{idx+1}/{len(jobs)}] Skipping job {job_id} ({title}) — no eligibility rows")
            continue

        logger.info(f"[{idx+1}/{len(jobs)}] Labeling: {title} (job_id={job_id}, {len(criteria_rows)} rows)")

        groups = group_criteria_by_group_id(criteria_rows)
        groups_summary = [
            {"group_id": gid, "lines": [describe_requirement(r, masters) for r in rows]}
            for gid, rows in sorted(groups.items())
        ]

        prompt = ROLE_NAME_PROMPT(title, description, groups_summary)
        try:
            ai_output_raw = call_gemini(prompt)
            ai_data = json.loads(strip_fences(ai_output_raw))
        except Exception as e:
            logger.error(f"  ❌ AI labeling failed for job {job_id} ({title}): {e}")
            continue

        role_by_group = {g["group_id"]: g["role_name"] for g in ai_data.get("groups", [])}
        debug_output.append({"job_id": job_id, "title": title, "role_by_group": role_by_group})

        sql_statements.append(f"-- Job {job_id}: {title}")
        for gid, rows in sorted(groups.items()):
            role_name = role_by_group.get(gid, "Unspecified")
            row_ids = [r["id"] for r in rows if "id" in r]

            if row_ids:
                id_list = ", ".join(str(i) for i in row_ids)
                sql_statements.append(
                    f"UPDATE job_eligibility_criteria SET role_name = '{sql_escape(role_name)}' "
                    f"WHERE id IN ({id_list});"
                )
            else:
                # Fallback if row ids weren't included in the export — matches on job_id + group_id instead
                sql_statements.append(
                    f"UPDATE job_eligibility_criteria SET role_name = '{sql_escape(role_name)}' "
                    f"WHERE job_id = {job_id} AND group_id = {gid};"
                )
        sql_statements.append("")  # spacer between jobs

        # Brief pause to respect API rate limits
        time.sleep(1)

    with open(OUTPUT_SQL, "w") as f:
        f.write("BEGIN;\n\n")
        f.write("\n".join(sql_statements))
        f.write("\nCOMMIT;\n")

    with open(OUTPUT_LOG, "w") as f:
        json.dump(debug_output, f, indent=2)

    logger.info(f"\n🎉 Done. Review {OUTPUT_SQL} before running it against the DB.")
    logger.info(f"   Per-group role_name reasoning saved to {OUTPUT_LOG} for a quick sanity check.")