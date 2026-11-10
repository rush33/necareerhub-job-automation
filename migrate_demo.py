"""
NE Career Hub — Mini Migration Script for Presentation
Reads old schema JSON, uses Gemini to extract missing age fields and map 
strict relational eligibility criteria from the HTML description, and publishes.
"""

import os
import json
import time
import re
import requests
import logging
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

# ─────────────────────────────────────────────
#  THE MIGRATION PROMPT
# ─────────────────────────────────────────────
def MIGRATION_PROMPT(title: str, description: str, masters: dict) -> str:
    return f"""You are an expert database architect migrating an old job post to a new relational schema.
Read the Job Title and HTML Description carefully. Extract the missing age fields and perfectly map the educational/experience criteria.

OLD JOB DATA:
- Title: {title}
- Description: {description}

MASTER TABLES (ID mapping):
- States: {json.dumps(masters['states'])}
- Sectors: {json.dumps(masters['sectors'])}
- Qualification Levels: {json.dumps(masters['qualification_levels'])}
- Degrees: {json.dumps(masters['degrees'])}
- Specializations: {json.dumps(masters['specializations'])}
- Functional Areas: {json.dumps(masters['functional_areas'])}

CRITICAL MAPPING RULES:

1. CERTIFICATE / DIPLOMA LOGIC (e.g., DCA, O Level, CCC, Computer Certificate):
   - If a job requires a Graduate + Computer Certificate (e.g., DCA/Diploma in Computer Application):
     - Requirement 1: Set `qualification_level_id` for "Graduate" or "Bachelor's Degree", `degree_id`: null, `specialization_id`: null.
     - Requirement 2: Set `qualification_level_id` for "Certificate" or "Diploma" (check `master_qualification_levels`), and map `specialization_id` or `degree_id` for DCA/Computer Applications from the master list.
     - Place BOTH in the SAME `group_id` (since both are required together - AND logic).

2. B.TECH / SPECIALIZATION BRANCH LOGIC:
   - When B.Tech / B.E. is specified with a stream (e.g., Civil, Mechanical, Electrical, Computer Science):
     - DO NOT default B.Tech to general IT.
     - Select `degree_id` corresponding to B.Tech / B.E.
     - Search `master_specializations` thoroughly and select the EXACT `specialization_id` matching the branch (Civil, Mechanical, etc.).
     - If multiple branches are allowed (e.g., "B.Tech in Civil OR Mechanical"), create SEPARATE `group_id`s for each branch OR separate requirements in distinct groups.

3. AND / OR GROUPING RULES:
   - AND Logic (Same group_id): Requirements that must be fulfilled TOGETHER (e.g., B.Tech Degree AND 2 years experience AND DCA Certificate).
   - OR Logic (Different group_ids): Alternative paths to qualify (e.g., Group 1: B.Tech Civil; Group 2: Diploma in Civil + 3 yrs exp).

4. STRICT NULLS FOR GENERAL GRADUATION:
   - If the text says "Graduation in any discipline" or "Bachelor's degree" without specifying a field, output `null` for BOTH `degree_id` and `specialization_id`.

5. FUNCTIONAL AREA LOGIC:
   - If `exp_years_required` is 0 (Fresher), `functional_area_id` MUST BE null.
   - If > 0, map to the most relevant functional area based on the post description.

SCHEMA TO OUTPUT (Output ONLY raw JSON):
{{
  "age_min": integer or null,
  "age_max_gen": integer or null,
  "sector_id": integer or null,
  "state_id": integer or null,
  "eligibility_criteria": [
    {{
      "group_id": 1,
      "requirements": [
        {{
          "qualification_level_id": integer,
          "degree_id": integer or null,
          "specialization_id": integer or null,
          "functional_area_id": integer or null,
          "exp_years_required": integer
        }}
      ]
    }}
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
            "systemInstruction": {"parts": [{"text": "You are a strict data mapper. Output ONLY valid JSON."}]},
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.1, "maxOutputTokens": 8192},
        },
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()["candidates"][0]["content"]["parts"][0]["text"]

def fetch_master_table(table_name: str) -> list:
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/{table_name}?select=id,name",
        headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    )
    r.raise_for_status()
    return r.json()

def load_all_masters():
    logger.info("Fetching master tables...")
    return {
        "states": fetch_master_table("states"),
        "sectors": fetch_master_table("master_sectors"),
        "qualification_levels": fetch_master_table("master_qualification_levels"),
        "degrees": fetch_master_table("master_degrees"),
        "specializations": fetch_master_table("master_specializations"),
        "functional_areas": fetch_master_table("master_functional_areas"),
    }

def publish_job(payload: dict):
    criteria_groups = payload.pop("eligibility_criteria", [])
    headers = {
        "Content-Type": "application/json",
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Prefer": "return=representation",
    }

    # Insert Job Post
    job_resp = requests.post(f"{SUPABASE_URL}/rest/v1/jobs", headers=headers, json=[payload])
    job_resp.raise_for_status()
    job_id = job_resp.json()[0]["id"]

    # Insert Eligibility Criteria
    if criteria_groups:
        criteria_rows = []
        for group in criteria_groups:
            for req in group.get("requirements", []):
                req["job_id"] = job_id
                req["group_id"] = group.get("group_id", 1)
                criteria_rows.append(req)
        
        if criteria_rows:
            crit_resp = requests.post(f"{SUPABASE_URL}/rest/v1/job_eligibility_criteria", headers=headers, json=criteria_rows)
            if not crit_resp.ok:
                logger.error("Criteria insert failed! Rolling back job %s...", job_id)
                requests.delete(f"{SUPABASE_URL}/rest/v1/jobs?id=eq.{job_id}", headers=headers)
                crit_resp.raise_for_status()

    return job_id

# ─────────────────────────────────────────────
#  MAIN EXECUTION
# ─────────────────────────────────────────────
if __name__ == "__main__":
    if not os.path.exists("old_jobs.json"):
        logger.error("old_jobs.json not found! Please create it.")
        exit(1)

    with open("old_jobs.json", "r") as f:
        old_jobs = json.load(f)

    masters = load_all_masters()
    
    for idx, job in enumerate(old_jobs):
        title = job.get("title", "Unknown Title")
        logger.info(f"\n[{idx+1}/{len(old_jobs)}] Migrating: {title}")
        
        # 1. Ask Gemini to read the HTML description and map the missing fields
        prompt = MIGRATION_PROMPT(title, job.get("description", ""), masters)
        try:
            ai_output_raw = call_gemini(prompt)
            ai_data = json.loads(strip_fences(ai_output_raw))
        except Exception as e:
            logger.error(f"  ❌ AI Mapping failed for {title}: {e}")
            continue
            
        # 2. Build the new payload by combining old valid fields and new AI fields
        new_payload = {
            "title": job.get("title"),
            "category": job.get("category"),
            "overview": job.get("overview"),
            "description": job.get("description"),
            "last_date": job.get("last_date"),
            "source_website": job.get("source_website"),
            "application_fee": int(job.get("application_fee", 0)),
            "salary_min": job.get("salary_min"),
            "salary_max": job.get("salary_max"),
            "keywords": json.loads(job.get("keywords", "[]")) if isinstance(job.get("keywords"), str) else (job.get("keywords") or []),
            "advertisement_url": json.loads(job.get("advertisement_url", "[]")) if isinstance(job.get("advertisement_url"), str) else job.get("advertisement_url"),
            "apply_url": json.loads(job.get("apply_url", "[]")) if isinstance(job.get("apply_url"), str) else job.get("apply_url"),
            "department_id": job.get("department_id"),
            
            # --- The new fields injected by Gemini ---
            "age_min": ai_data.get("age_min"),
            "age_max_gen": ai_data.get("age_max_gen"),
            "sector_id": ai_data.get("sector_id"),
            "state_id": ai_data.get("state_id"),
            "eligibility_criteria": ai_data.get("eligibility_criteria", [])
        }
        
        # Append a short timestamp to slug to ensure it doesn't collide if you are keeping the old DB alive during testing
        new_payload["slug"] = f"{job.get('slug', 'job')}-{int(time.time())}"

        # 3. Publish to Supabase
        try:
            job_id = publish_job(new_payload)
            logger.info(f"  ✅ Successfully published! New Job ID: {job_id}")
        except requests.exceptions.HTTPError as e:
            # This will catch the 400 Bad Request and print the exact Supabase error message
            logger.error(f"  ❌ Supabase rejected the insert for {title}.")
            try:
                error_details = e.response.json()
                logger.error(f"     Reason: {json.dumps(error_details, indent=2)}")
            except:
                logger.error(f"     Reason: {e.response.text}")
        except Exception as e:
            logger.error(f"  ❌ Unexpected error for {title}: {e}")
            
        # Brief pause to respect API rate limits
        time.sleep(2)
        
    logger.info("\n🎉 Migration batch complete!")