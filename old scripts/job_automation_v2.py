"""
NE Career Hub — Job Automation Pipeline (Python CLI)
Uses pdfplumber for text extraction, Gemini for OCR fallback, 
and a two-step Agentic LLM workflow for perfect relational DB mapping.
"""

import os
import sys
import re
import json
import time
import hashlib
import logging
import argparse
import textwrap
import requests
import pdfplumber
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
from google import genai

load_dotenv()

# ─────────────────────────────────────────────
#  LOGGING
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("pipeline.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
#  CONFIG & INITIALIZATION
# ─────────────────────────────────────────────
GOOGLE_MODEL   = os.environ.get("GOOGLE_MODEL", "gemini-1.5-flash")
GOOGLE_API_KEY = os.environ["GOOGLE_API_KEY"]
SUPABASE_URL   = os.environ["SUPABASE_URL"]
SUPABASE_KEY   = os.environ["SUPABASE_KEY"]

try:
    gemini_client = genai.Client(api_key=GOOGLE_API_KEY)
except Exception as e:
    logger.error("Failed to initialize Google GenAI client. Ensure google-genai is installed.")
    gemini_client = None


# ─────────────────────────────────────────────
#  PROMPTS (The Agentic Workflow)
# ─────────────────────────────────────────────
EXTRACT_PROMPT = """You are a government job notification extraction engine.
Extract ALL of the following fields from the provided document:

1. Post Name(s) [RULE: Format strictly as a vertical bulleted list. NEVER group into a single paragraph.]
2. Total Vacancy
3. Who Should Apply
4. Vacancy Breakdown (Category/state-wise. If national level, only show breakdown for NE India states, ignore the rest)
5. Pay Scale (Specific components: Starting Basic Pay, Range, Allowances, CTC. If only 7th CPC Pay Levels like "Level 8" are given, you MUST convert them to actual INR salary ranges like "₹47,600 - ₹1,51,100")
6. Educational Qualification & Experience [CRITICAL: For every post mentioned, extract BOTH the required educational degree AND the required experience duration/details. If no experience is required, explicitly write "Fresher eligible / No experience required".]
7. Age Limit (with cutoff date)
8. Age Relaxation (category-wise, table format)
9. Application Fee (Search entire document; set to 0 if free)
10. Selection Process (Break down into distinct step-by-step phases)
11. Exam Pattern
12. Important Dates
13. How to Apply (steps)
14. Why This Matters for NE Applicants
15. Important Notes / Hidden Conditions
16. Important Links Table
17. Frequently Asked Questions (FAQs)
 
HARD RULES:
- NEVER skip Application Fee (default to 'not required' if not mentioned).
- NEVER leave Pay Level without converting to monetary salary range.
- NEVER include serial number columns in tables.
- If data for a non-essential section is missing, write "Not mentioned". If 'Exam Pattern' is missing, skip that section entirely.
- EXCLUSION RULE: Ignore blank application templates or annexures at the end of PDFs.
- IF THERE IS A VACANCY TABLE OR ANY IMPORTANT TABLE IN THE SOURCE, REPLICATE IT EXACTLY.

EDITORIAL QUICK TAKE RULES:
- MANDATORY LENGTH: EXACTLY 3 to 4 sentences.
- Help the user decide in 10 seconds with a direct, human tone.

WHO SHOULD APPLY RULES:
- Write EXACTLY 3 checklist points, each starting with ✅ emoji.
- Must cover: qualification/subject match, application mode, and deadline.

WHY THIS MATTERS FOR NE APPLICANTS RULES:
- Write 2-3 sentences explaining regional/state relevance.

FAQ RULES:
- 3 to 5 Q&A pairs formatted strictly as:
  Q: [Question]
  A: [Answer]
 
OUTPUT FORMAT — use EXACTLY these ### headings:
### Post Name(s)
### Vacancy Details
### Pay Scale
### Educational Qualification & Experience
### Who Should Apply
### Age Limit
### Age Relaxation
### Application Fee
### Selection Process
### Exam Pattern
### Important Dates
### How to Apply
### Why This Matters for NE Applicants
### Important Notes
### Important Links Table
### Frequently Asked Questions (FAQs)"""

def HTML_PROMPT(text):
    return f"""You are an expert UI/UX content formatter. Convert this extracted job notification into beautifully structured, highly scannable TipTap-compatible HTML.
    
STRICT FORMATTING RULES:
- ONLY use <h3> for section headings. Insert an <hr> tag immediately before every <h3> (except the very first one).
- Wrap ALL list item content and table cell text in <p> tags (e.g., <li><p>Content</p></li>, <td><p>Value</p></td>).
- SCANNABILITY: Use <strong> strategically to highlight critical terms (job titles, dates, numbers, major degrees). Do NOT bold entire sentences or paragraphs.

EDUCATIONAL QUALIFICATION & EXPERIENCE FORMATTING RULE:
- If qualifications are simple or generic for all posts (e.g. "Graduate in any discipline"), format as a bulleted list (<ul><li><p>...</p></li></ul>).
- If qualifications/experience are specified separately across multiple posts or roles:
  Format as a multi-column table with EXACTLY these headers:
  • Post
  • Educational Qualification
  • Min. Experience
  (If experience is not mentioned for a post, write "No experience required" in the Experience column).
- Format "OR" / "AND" conditions inside cells as: <br><strong>OR</strong> / <strong>AND</strong>.

LIST FORMATTING RULES:
- "Selection Process", "Who Should Apply", "Why This Matters for NE Applicants", and "How to Apply" MUST be unordered lists (<ul><li><p>...</p></li></ul>). Retain ✅ emojis in "Who Should Apply".

FAQS FORMATTING RULE:
- Format each Q&A pair strictly as:
  <p><strong>Q: [Question text]</strong></p>
  <p>A: [Answer text]</p>
  (Do NOT use lists or tables for FAQs).

TABLE MANDATE & STYLING:
- Transform Vacancy Details, Pay Scale, Age Relaxation, and Multi-Post Qualifications into multi-column relational tables.
- Tables MUST use EXACTLY this HTML structure and classes:
<table class="w-full border-collapse border border-divider my-4" style="min-width: 50px;">
  <tbody>
    <tr>
      <th class="bg-default-100 border border-divider p-2 font-bold text-left" colspan="1" rowspan="1"><p>Header</p></th>
    </tr>
    <tr>
      <td class="border border-divider p-2 relative" colspan="1" rowspan="1"><p>Value</p></td>
    </tr>
  </tbody>
</table>
- NO serial number columns. NO inline styles except those specified above.
- NO <h1>, NO <h2>, NO markdown code fences.
- Output ONLY raw HTML.

CONTENT TO CONVERT:
{text}"""
def BASE_JSON_PROMPT(text, links):
    links_str = "\n".join(f'- Label: "{l["label"]}" → {l["url"]}' for l in links)
    return f"""You are a data extraction engine. Generate a BASE JSON object for a job posting.
 
USER-PROVIDED LINKS:
{links_str}

Extract the raw text for department, sector, state, and eligibility criteria. We will map them to database IDs in the next step.

SCHEMA:
{{
  "slug": "kebab-base-slug-with-org-acronym-no-suffix",
  "title": "Organization Recruitment Year – Shortened Post Name(s) (Keep concise, max 70 chars)",
  "category": "one of: central, state, public-sector",
  "overview": "3-4 sentence professional summary of the notification",
  "last_date": "ISO 8601 UTC date (e.g. 2026-05-01T18:29:59+00:00)",
  "source_website": "best guess URL of official website",
  "application_fee": integer rupees (0 if free or calculate standard minimum),
  "salary_min": integer monthly minimum rupees,
  "salary_max": integer monthly maximum rupees,
  "age_min": integer (minimum age allowed, usually 18 or 21),
  "age_max_gen": integer (maximum age for general category),
  "keywords": ["array", "of", "5-7", "lowercase", "search", "tags"],
  "advertisement_url": [{{\"url\": \"notice links\", \"label\": \"Notice\"}}],
  "apply_url": [{{\"url\": \"apply links\", \"label\": \"Apply\"}}],
  
  "raw_post_names": "Extract the specific names of the posts",
  "raw_department_text": "e.g. Staff Selection Commission (SSC)",
  "raw_sector_text": "e.g. Government Administration",
  "raw_state_text": "e.g. Assam or All India",
  "raw_eligibility_text": "Include ALL exact rules for degrees, experience, and alternative paths."
}}
 
CRITICAL RULE: Output ONLY valid JSON. No markdown code fences (like ```json), no conversation.
 
JOB NOTIFICATION TEXT:
{text}"""
def RELATIONAL_MAPPING_PROMPT(base_json: dict, masters: dict) -> str:
    return f"""You are an expert database architect specializing in the Indian education system and government recruitment rules.
Your task is to map raw extracted job data into strict relational database IDs based on the provided Master Tables.

RAW DATA TO MAP:
- Post Names: {base_json.get('raw_post_names')}
- Department Text: {base_json.get('raw_department_text')}
- Sector Text: {base_json.get('raw_sector_text')}
- State Text: {base_json.get('raw_state_text')}
- Eligibility Rules: {base_json.get('raw_eligibility_text')}
- Source URLs: {base_json.get('source_website')}

MASTER TABLES (ID mapping):
- Departments (match by name similarity or hierarchical fallback): {json.dumps(masters['departments'])}
- States: {json.dumps(masters['states'])}
- Sectors: {json.dumps(masters['sectors'])}
- Qualification Levels: {json.dumps(masters['qualification_levels'])}
- Degrees: {json.dumps(masters['degrees'])}
- Specializations: {json.dumps(masters['specializations'])}
- Functional Areas: {json.dumps(masters['functional_areas'])}

STRICT ELIGIBILITY MAPPING RULES (DO NOT GUESS):
1. OR Logic (group_id): Different alternative paths must have different group_ids. (e.g. Degree in IT = group 1, Degree in Econ = group 2).
2. AND Logic (same group): If a post requires a Degree AND a Diploma, they go in the SAME group_id.
3. Indian Context Splits: 
   - If a requirement is "Statistics", you MUST split it into BOTH "B.A." and "B.Sc." as separate alternative paths (different group_ids).
   - If a requirement is "IT/CS", you MUST split it into "B.Tech", "B.Sc.", and "BCA" as separate alternative paths.
4. STRICT NULLS for Specialization: Do NOT hallucinate specializations. If the text says "Bachelor's in any subject" or "Degree from recognized university", output null for BOTH Degree and Specialization.
5. FUNCTIONAL AREA LOGIC (CRITICAL):
   - If `exp_years_required` is 0 (Fresher), `functional_area_id` MUST BE null.
   - If `exp_years_required` > 0 (Experienced), map `functional_area_id` to the most relevant domain based on the required experience field or the Post Names.
6. Deduplication: Do not duplicate identical criteria groups if multiple posts have the exact same requirement.

DEPARTMENT MATCHING RULE (BE HIGHLY FLEXIBLE):
7. You MUST prioritize finding a valid `department_id` to avoid nulls. Use this hierarchy:
   - Level 1 (Fuzzy): Match by name similarity.
   - Level 2 (Umbrella): If recruited by an overarching body (e.g., "Assam Home Guards" via "APSC" or "SSC"), map to the umbrella body. Check URLs for clues.
   - Level 3 (State Fallback): If it is a random local body (e.g., "MPCB Manipur" or "Arts and Crafts") that doesn't exist, safely map it to the broader state government department (e.g., "Government of Manipur") if available in the master list.
8. ESSENTIAL VS DESIRABLE: You MUST IGNORE any qualifications marked as "Desirable" or "Preferred". ONLY map mandatory/essential qualifications. If a post says "Bachelor's Degree (Desirable: MBA)", you ONLY map the Bachelor's Degree.
9. NO LAZINESS (MAP EVERY SUBJECT): If a post lists 10 different accepted subjects (e.g., Demography, AI, Operation Research), you MUST map EVERY SINGLE ONE as a separate alternative path (different group_ids). DO NOT truncate or summarize the list.
10. If for teaching jobs the qualification states as per UGC norms, web search what it means and apply here appropriately

SCHEMA TO OUTPUT (Output ONLY raw JSON):
{{
  "department_id": "matched uuid or null",
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
          "exp_years_required": integer (0 if none)
        }}
      ]
    }}
  ]
}}"""
# ─────────────────────────────────────────────
#  HELPERS & AI EXECUTION
# ─────────────────────────────────────────────
def content_hash(text: str) -> str:
    return hashlib.md5(text[:5000].encode()).hexdigest()[:8]

def strip_fences(text, fence="json"):
    return re.sub(rf"```{fence}?\n?", "", text).replace("```", "").strip()

def call_gemini(prompt: str, system: str = "You are a helpful assistant.", retries: int = 5) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GOOGLE_MODEL}:generateContent?key={GOOGLE_API_KEY}"
    for attempt in range(retries):
        try:
            resp = requests.post(
                url,
                headers={"Content-Type": "application/json"},
                json={
                    "systemInstruction": {"parts": [{"text": system}]},
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": 0.1, "maxOutputTokens": 8192},
                },
                timeout=120,
            )
            resp.raise_for_status()
            return resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        except requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else 0
            if status == 429 or status >= 500:
                wait = (2 ** attempt) * 5 
                logger.warning("    ⚠  API error (%s) on attempt %d/%d — retrying in %ds...", status, attempt + 1, retries, wait)
                time.sleep(wait)
            else:
                raise
    raise RuntimeError(f"Gemini API failed after {retries} retries")

def extract_with_gemini_vision(pdf_path: str) -> str:
    """Uploads a scanned PDF to Google and extracts text natively via OCR."""
    if not gemini_client:
        logger.error("    ❌ Google GenAI client not loaded. Cannot perform OCR.")
        return ""
        
    logger.info("    ☁️ Uploading scanned PDF to Gemini for native OCR processing...")
    try:
        pdf_file = gemini_client.files.upload(file=pdf_path)
        
        while pdf_file.state.name == "PROCESSING":
            time.sleep(2)
            pdf_file = gemini_client.files.get(name=pdf_file.name)
            
        logger.info("    ✓ File processed. Extracting text...")
        response = gemini_client.models.generate_content(
            model=GOOGLE_MODEL,
            contents=[pdf_file, "Extract all the text from this document accurately. Preserve the reading order. Output ONLY the raw text."]
        )
        
        gemini_client.files.delete(name=pdf_file.name)
        return response.text
    except Exception as e:
        logger.error("    ❌ Gemini OCR failed: %s", e)
        return ""

# ─────────────────────────────────────────────
#  SUPABASE LOGIC
# ─────────────────────────────────────────────
def fetch_master_table(table_name: str, cols: str = "id,name") -> list:
    if not SUPABASE_URL or not SUPABASE_KEY: return []
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/{table_name}?select={cols}",
        headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()

def load_all_masters():
    logger.info("    Fetching master tables for Agentic mapping...")
    return {
        "departments": fetch_master_table("departments"), # Double check if this needs to be master_departments in your DB
        "states": fetch_master_table("states"),           # Double check if this needs to be master_states in your DB
        "sectors": fetch_master_table("master_sectors"),
        "qualification_levels": fetch_master_table("master_qualification_levels"),
        "degrees": fetch_master_table("master_degrees"),
        "specializations": fetch_master_table("master_specializations"),
        "functional_areas": fetch_master_table("master_functional_areas"),
    }

def publish_job(payload: dict) -> dict:
    """Inserts Job, captures ID, then inserts Eligibility Criteria, handling rollbacks."""
    criteria_groups = payload.pop("eligibility_criteria", [])
    
    # --- AGGRESSIVE CLEANUP OF OLD SCHEMA FIELDS ---
    legacy_keys = [
        "min_qualification", 
        "min_qualification_rank", 
        "sub_category", 
        "state", 
        "isSaved"
    ]
    for key in legacy_keys:
        payload.pop(key, None)
    
    headers = {
        "Content-Type": "application/json",
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Prefer": "return=representation",
    }

    # 1. Insert Job Post
    job_resp = requests.post(f"{SUPABASE_URL}/rest/v1/jobs", headers=headers, json=[payload], timeout=15)
    
    # Add this block to catch the EXACT error reason
    if not job_resp.ok:
        logger.error(f"Supabase Job Insert Failed! Reason: {job_resp.text}")
        
    job_resp.raise_for_status()
    inserted_job = job_resp.json()[0]
    job_id = inserted_job["id"]

    # 2. Insert Eligibility Criteria Rows
    if criteria_groups:
        criteria_rows = []
        for group in criteria_groups:
            group_id = group.get("group_id", 1)
            for req in group.get("requirements", []):
                req["job_id"] = job_id
                req["group_id"] = group_id
                criteria_rows.append(req)
        
        if criteria_rows:
            crit_resp = requests.post(f"{SUPABASE_URL}/rest/v1/job_eligibility_criteria", headers=headers, json=criteria_rows, timeout=15)
            if not crit_resp.ok:
                logger.error(f"    ⚠ Criteria insert failed! Reason: {crit_resp.text}")
                logger.error("    Rolling back job %s...", job_id)
                requests.delete(f"{SUPABASE_URL}/rest/v1/jobs?id=eq.{job_id}", headers=headers)
                crit_resp.raise_for_status()

    return inserted_job

# ─────────────────────────────────────────────
#  PIPELINE EXECUTOR
# ─────────────────────────────────────────────
def run_pipeline(pdf_paths=None, source_url=None, links=None, publish=False, output_dir="."):
    if not pdf_paths and not source_url: raise ValueError("Provide --pdf or --url")
    links = links or []
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # ── STEP 0: Fetch content & OCR ────────────
    logger.info("\n📥 Step 0: Fetching content...")
    raw_text = ""
    if pdf_paths:
        for pdf_file in pdf_paths:
            logger.info("    Extracting PDF: %s", pdf_file)
            pdf_text = ""
            with pdfplumber.open(pdf_file) as pdf:
                for page in pdf.pages:
                    t = page.extract_text()
                    if t: pdf_text += t + "\n"
            
            # Trigger OCR if text is basically empty (scanned image)
            if len(pdf_text.strip()) < 150:
                logger.warning("    ⚠ Scanned image detected. Triggering native Gemini OCR...")
                pdf_text = extract_with_gemini_vision(pdf_file)
                
            raw_text += pdf_text + "\n"

    src_hash = content_hash(raw_text)
    
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    debug_dir = out / f"debug_{timestamp}"
    debug_dir.mkdir(exist_ok=True)

    # ── STEP 1: Fetch Master DB Tables ─────────
    logger.info("\n🏛️  Step 1: Loading Master Database Tables...")
    masters = load_all_masters()

    # ── STEP 2: Main AI Extraction ─────────────
    logger.info("\n🤖 Step 2: Extracting raw job details...")
    extracted_text = call_gemini(EXTRACT_PROMPT + f"\n\nCONTENT:\n{raw_text}")
    (debug_dir / "2_extracted.txt").write_text(extracted_text, encoding="utf-8")

    # ── STEP 3: Generate TipTap HTML ───────────
    logger.info("\n🧱 Step 3: Generating HTML...")
    html_raw = call_gemini(HTML_PROMPT(extracted_text))
    generated_html = strip_fences(html_raw, "html")

    # ── STEP 4: Build Base JSON ────────────────
    logger.info("\n🗂️  Step 4: Building Base JSON...")
    base_json_raw = call_gemini(BASE_JSON_PROMPT(extracted_text, links))
    base_json_str = strip_fences(base_json_raw, "json")
    
    try:
        payload = json.loads(base_json_str)
    except Exception as e:
        raise ValueError(f"Gemini returned invalid BASE JSON: {e}\n\nRaw:\n{base_json_str[:500]}")

    payload["description"] = generated_html
    if payload.get("slug"):
        payload["slug"] = f"{payload['slug']}-{src_hash}"

    # ── STEP 5: Agentic Relational Mapping ─────
    logger.info("\n🧠 Step 5: Agentic Relational Mapping (Call 2)...")
    mapping_prompt = RELATIONAL_MAPPING_PROMPT(payload, masters)
    mapped_json_raw = call_gemini(mapping_prompt, system="You are a strict data mapper. Output ONLY JSON.")
    mapped_json_str = strip_fences(mapped_json_raw, "json")
    
    (debug_dir / "5_mapped_relations.json").write_text(mapped_json_str, encoding="utf-8")
    
    try:
        mapped_data = json.loads(mapped_json_str)
    except Exception as e:
        logger.error("    ❌ Gemini mapping failed to output valid JSON. Proceeding with nulls.")
        mapped_data = {"department_id": None, "state_id": None, "sector_id": None, "eligibility_criteria": []}

    # Merge mapped IDs into the final payload
    payload["department_id"] = mapped_data.get("department_id")
    payload["state_id"] = mapped_data.get("state_id")
    payload["sector_id"] = mapped_data.get("sector_id")
    payload["eligibility_criteria"] = mapped_data.get("eligibility_criteria", [])

    # Clean up temporary raw text fields from base JSON before saving
    keys_to_remove = ["raw_post_names", "raw_department_text", "raw_sector_text", "raw_state_text", "raw_eligibility_text"]
    for key in keys_to_remove:
        payload.pop(key, None)

    # ── SAVE OUTPUTS ───────────────────────────
    json_file = out / f"payload_{timestamp}.json"
    json_file.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("\n✅ JSON Payload Ready   → %s", json_file)

    # ── STEP 6: Publish ────────────────────────
    if not publish:
        logger.info("⚡ Dry run complete. Review JSON before publishing.")
    else:
        logger.info("\n🚀 Step 6: Publishing to Supabase...")
        result = publish_job(payload)
        logger.info("    ✓ Posted! Job ID: %s", result.get("id"))

    return payload

def parse_links(raw: list) -> list:
    out = []
    for item in (raw or []):
        if "::" in item:
            label, url = item.split("::", 1)
            out.append({"label": label.strip(), "url": url.strip()})
        else:
            out.append({"label": "Link", "url": item.strip()})
    return out

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="NE Career Hub — Job Automation Pipeline")
    parser.add_argument("--pdf", action="append", help="Path to notification PDF")
    parser.add_argument("--url", help="URL of job notification page")
    parser.add_argument("--link", action="append", help="Label::URL")
    parser.add_argument("--out", default=".", help="Output directory")
    parser.add_argument("--publish", action="store_true", help="Publish to Supabase")
    parser.add_argument("--from-json", help="Skip AI steps and publish an existing payload JSON directly")
    
    args = parser.parse_args()

    if args.from_json:
        with open(args.from_json) as f:
            payload = json.load(f)
        result = publish_job(payload)
        logger.info("✓ Published! ID: %s", result.get("id"))
        sys.exit(0)

    links = parse_links(args.link)
    
    try:
        run_pipeline(
            pdf_paths=args.pdf,
            source_url=args.url,
            links=links,
            publish=args.publish,
            output_dir=args.out,
        )
    except Exception as e:
        logger.error("\n❌ Pipeline failed: %s", e)
        sys.exit(1)