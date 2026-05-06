"""
NE Career Hub — Job Automation Pipeline (Python CLI)
Uses pdfplumber for PDF text extraction and DeepSeek via OpenRouter for AI steps.
"""

import os
import re
import json
import time
import random
import string
import hashlib
import logging
import argparse
import textwrap
import requests
import pdfplumber
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()

# ─────────────────────────────────────────────
#  LOGGING — structured, writes to file + console
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
#  CONFIG — set via env vars or CLI args
# ─────────────────────────────────────────────
OPENROUTER_API_KEY = os.environ["GOOGLE_API_KEY"]
OPENROUTER_BASE    = os.environ["GOOGLE_BASE"]
OPENROUTER_MODEL   = os.environ["GOOGLE_MODEL"]

SUPABASE_URL   = os.environ["SUPABASE_URL"]
SUPABASE_KEY   = os.environ["SUPABASE_KEY"]
SUPABASE_TABLE = os.environ["SUPABASE_TABLE"]

GOOGLE_MODEL = os.environ["GOOGLE_MODEL"]
GOOGLE_API_KEY = os.environ["GOOGLE_API_KEY"]


# ─────────────────────────────────────────────
#  PROMPTS (identical to HTML version)
# ─────────────────────────────────────────────
EXTRACT_PROMPT = """You are a government job notification extraction engine.
Extract ALL of the following fields from the provided document:
1. Post Name(s), 2. Total Vacancy, 3. Vacancy Breakdown (category/state-wise),
4. Pay Scale (with actual salary ranges, search entire document — NEVER MISS THIS),
5. Educational Qualification, 6. Experience Required,
7. Age Limit (with cutoff date), 8. Age Relaxation (category-wise) (mention if applies, important),
9. Application Fee (search entire document — NEVER MISS THIS),
10. Selection Process, 11. Exam Pattern, 12. Important Dates,
13. How to Apply (steps), 14. Important Notes / hidden conditions
 
HARD RULES:
- NEVER skip Application Fee. If not found, set value to 0
- NEVER skip Pay Scale / Salary. If not found, search document again to double check
- NEVER leave Pay Level without converting to salary range
- NEVER include serial number columns in tables
- If data missing: write "Not mentioned"
- Scan ENTIRE document including last pages and footnotes
- If a section like 'Exam Pattern' is not mentioned, skip that section entirely, dont write 'Not Mentioned'
 
OUTPUT FORMAT — use these exact ### headings:
### Post Name(s)
### Vacancy Details
### Pay Scale
### Educational Qualification
### Age Limit
### Application Fee
### Selection Process
### Exam Pattern
### Important Dates
### How to Apply
### Important Notes"""

def HTML_PROMPT(text):
    return f"""Convert this extracted job notification into clean TipTap-compatible HTML.
STRICT RULES:
- ONLY use h3 for section headings. Insert an <hr> tag immediately before every <h3> (except the very first one).
- Wrap ALL list item content in <p> tags (e.g., <li><p>Content</p></li>).
- For Educational Qualifications, format "OR" conditions using <br><strong>OR</strong>.
- Transform structured data (Vacancy Details, Pay Scale, Experience, Age Limit, Application Fee, Exam Pattern) into multi-column relational tables (e.g., Columns for Post | Vacancies | UR | SC | etc., or Post | Pay Level). Do NOT create vertical key-value tables.
- Tables MUST use EXACTLY this format and classes:
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
- ALL <th> tags MUST have `class="bg-default-100 border border-divider p-2 font-bold text-left" colspan="1" rowspan="1"`
- ALL <td> tags MUST have `class="border border-divider p-2 relative" colspan="1" rowspan="1"`
- Wrap ALL cell content in <p> tags
- NO serial number columns
- Use <ul><li> for bullet lists, <p> for paragraphs, <strong> for emphasis
- NO inline styles except those specified above
- NO h1, NO h2, NO markdown, NO promotional language
- If a section has no data, output: <p>Not mentioned in the notification.</p> — do NOT invent data.
- Output ONLY raw HTML — no explanation, no markdown code fences
 
CONTENT:
{text}"""

def JSON_PROMPT(text, dept_list, links):
    dept_json = json.dumps(
        [{"id": d["id"], "name": d["name"], "image_url": d.get("image_url")} for d in dept_list],
        indent=2
    )
    links_str = "\n".join(f'- Label: "{l["label"]}" → {l["url"]}' for l in links)
    return f"""You are a data extraction engine. Generate a JSON object for a Supabase jobs table.
 
OUTPUT: valid JSON only. No explanation, no markdown fences, no comments.
 
AVAILABLE DEPARTMENTS (match by name similarity):
{dept_json}
 
USER-PROVIDED LINKS:
{links_str}

Follow these STRICT RULES for each field:

1. "title": 
   - Exact format: "[Dept] Recruitment [Year] – [Total Vacancies] [Post Name(s)] Posts"
   - [Dept] Rules:
	1.	Acronym + Location: Use standard prefix acronyms (NIT, IIT) but ALWAYS keep the specific location/state as a full word. (e.g., "NIT Sikkim" not "NITS").
	2.	State Gov: Format as [State] [Core Function] (e.g., "Department of Health, Meghalaya" -> "Meghalaya Health"), State PSC are popular, hence leave as acronyms (e.g., APSC, NPSC).
	3.	Standalone: Use pure acronyms only if there's no location suffix (e.g., "SSC", "ISRO").
   - [Total Vacancies]: Calculate exact integer sum of all posts. NEVER use placeholders.
   - [Post Name]: Do not mention all posts and make the title long, just one general word. e.g NIT Sikkim Recruitment 2026 - 30 Faculty Posts


2. "overview": 
   - Write a professional, 3-to-4 sentence summary.
   - MUST include: Full Organization Name (and abbreviation), the specific Post Names, the recruitment type (e.g., "direct recruitment"), the exact Total Vacancies, the Pay Levels AND exact Salary Ranges, and the final Last Date to apply. 
   - Example format: "[Full Name] ([Abbreviation]) has released a recruitment notification for [Posts]... A total of [X] vacancies are available... The pay scale is [Level] ([Range])..."

3. "advertisementUrl" & "applyUrl":
   - Extract the EXACT deep links to the PDF document and the actual application portal. 
   - Do NOT just output the homepage or root domain (e.g., do not just write "https://recruitment.nic.in/"). Look for links ending in .pdf or specific portal endpoints.

4. "min_qualification" & "sub_category":
   - Analyze the "Educational Qualification" section to identify the lowest acceptable degree required for the role.
   - If the requirement lists multiple degrees (e.g., "B.Tech or Bsc. Chemistry"), set min_qualification to the lower level (e.g., Graduate).
   - Logic for "sub_category": Do not determine the sub-category based solely on the educational degree. Instead, analyze the Posting Department and the Nature of the Job.
   - Primary Rule: If the department is related to Banking, Finance, Police, or Healthcare, set the sub_category to that specific industry (e.g., "Banking," "Law Enforcement," "Finance").
   - Technical Rule: If the primary requirement is a technical degree (B.Tech/M.Tech/BE) and the department is generic or technical, set the sub_category to "Engineering."
   - Fallback: Use the core function of the department to resolve any ambiguity between multiple educational requirements.

5. "application_fee":
   - If there is a fee waiver for certain categories (e.g., SC/ST/Women pay 0), but a standard fee for General candidates, accurately reflect the standard fee OR set to 0 if you are calculating the base minimum fee. (Align this with your database schema's expected behavior).

6. "last_date": The given last date is in IST. You must convert the IST to equivalent UTC for the next step.

7. "min_qualification_rank":
 	Exact Rank Mapping: You must assign a rank based on the min_qualification you found earlier.
 	0 : Any / No specific qualification mentioned
 	10 : 8th Pass
 	20 : 10th Pass / Matriculation / HSLC
 	30 : 12th Pass / Higher Secondary / 10+2
 	32 : ITI (Industrial Training Institute)
 	35 : Diploma (Polytechnic, etc.)
 	40 : Graduate (B.A, B.Sc, B.Com, generic degrees)
 	45 : Engineering (B.E, B.Tech)
 	50 : Post Graduate (M.A, M.Sc, M.Com, M.Tech)
 	55 : Medical (MBBS, BDS, Nursing)

   CRITICAL OUTPUT RULE: Output ONLY the raw JSON object. Do not include any conversational filler, introductory text (like "Here is the HTML..."), or markdown code blocks (like ```json). Under no circumstances should you prepend conversational text to the HTML string inside the description field.

   Example of correct "description" field value:
    "description": "<h3>Post Name(s)</h3><p>...</p>"
    NOT:
    "description": "Here is the HTML: <h3>Post Name(s)</h3>.." 

   SELF-CHECK before outputting: verify that salary_min and salary_max are plain integers in rupees per month, last_date is a real date extracted from the document (not invented), and slug contains no spaces or special characters.

SCHEMA:
{{
  "slug": "kebab-base-slug-with-job-department-acronym-no-suffix",
  "title": "Org Recruitment Year – (Vacancy Number) Post Names (concise)",
  "advertisement_url": [{{\"url\": \"links that look like official notices/PDFs\", \"label\": \"Notice\"}}],
  "apply_url": [{{\"url\": \"links that look like apply/registration links\", \"label\": \"Apply\"}}],
  "category": "one of: central, state, public-sector",
  "overview": "4-5 line summary: vacancies + pay scale + last date",
  "last_date": "ISO 8601 e.g. 2026-05-01T18:29:59+00:00 (use 18:29:59+00:00 if time unknown)",
  "source_website": "best guess from links or org website",
  "department_id": "matched dept id from list OR null if no match",
  "min_qualification": "one of: 8th_pass,10th_pass,12th_pass,iti,diploma,graduate,post_graduate,engineering,medical or 'all'",
  "state": "one of: assam,arunachal,meghalaya,nagaland,manipur,mizoram,tripura,sikkim or 'all' for central govt",
  "sub_category": "one of: psu,railway,defence,banking,teaching,police,engineering,medical,clerical,postal,administrative,upsc_ssc,judiciary,non_technical,research,healthcare,sports",
  "application_fee": integer rupees (0 if free, use SC/ST fee or the least),
  "salary_min": integer monthly minimum rupees,
  "salary_max": integer monthly maximum rupees,
  "keywords": ["array", "of", "5-7", "relevant", "lowercase", "search", "tags", "and", "job department acronym"],
  "min_qualification_rank": integer rank corresponding to the min_qualification
}}
 
JOB NOTIFICATION TEXT:
{text}"""

def DEPT_MATCH_PROMPT(org_name, dept_list):
    dept_json = json.dumps([{"id": d["id"], "name": d["name"]} for d in dept_list])
    return f"""Given this organization name extracted from a job notification: "{org_name}"
 
And this list of existing departments:
{dept_json}
 
Does any existing department match this organization?
Reply with ONLY valid JSON — no explanation, no fences:
If match found: {{"matched": true, "id": "dept-id", "name": "dept-name", "confidence": "high|medium|low"}}
If no match: {{"matched": false, "suggestedName": "full official name of the organization"}}"""


# ─────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────
def short_id():
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=4))

def content_hash(text: str) -> str:
    """Stable 8-char hash of source content — used for idempotency checks."""
    return hashlib.md5(text[:5000].encode()).hexdigest()[:8]

def strip_fences(text, fence="json"):
    return re.sub(rf"```{fence}?\n?", "", text).replace("```", "").strip()

def format_telegram_message(payload: dict) -> str:
    """Use AI to format a clean Telegram message from the payload."""
    prompt = f"""Format the following job notification into this exact structure:

🔔 Recruitment Title – Total Posts
🏢 Organization Name
🎓 [All specific degrees in qualification]
📅 Last Date: [Last Date in DD MonthName YYYY]
View Full Details & Apply Here 👇
https://necareerhub.in/job/[job-slug]

Rules:
- Keep it concise and clean
- Extract only key info (title, org, qualification, last date)
- Use simple qualification summary (e.g., "B.Tech/MCA", "Ph.D.", "Graduate")
- Do not include extra details like fees, selection process, etc.
- Maintain emojis and formatting exactly
- Output ONLY the message, nothing else

JOB DATA:
Title: {payload.get('title')}
Organization: {payload.get('departmentName')}
Slug: {payload.get('slug')}
Last Date: {payload.get('lastDate')}
Min Qualification: {payload.get('minQualification')}
Overview: {payload.get('overview', '')[:300]}
"""
    return call_deepseek(prompt, system="You are a Telegram message formatter. Output only the final message text, no explanation.")


def send_telegram(message: str) -> bool:
    """Send a message to Telegram channel."""
    token   = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        logger.warning("    ⚠  TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set — skipping")
        return False

    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        },
        timeout=10,
    )

    if not r.ok:
        logger.error("    Telegram error %s: %s", r.status_code, r.text)
        return False

    logger.info("    ✓ Telegram message sent")
    return True


# ─────────────────────────────────────────────
#  PAYLOAD VALIDATION
# ─────────────────────────────────────────────
def validate_payload(payload: dict):
    """
    Validate AI-generated payload before publishing.
    Raises ValueError listing all problems found.
    """
    errors = []

    # Required fields must be present and non-empty
    for field in ["title", "slug", "last_date", "overview", "salary_min", "salary_max"]:
        if not payload.get(field) and payload.get(field) != 0:
            errors.append(f"Missing required field: {field}")

    # Integer type checks
    for int_field in ["salary_min", "salary_max", "application_fee"]:
        val = payload.get(int_field)
        if val is not None and not isinstance(val, int):
            errors.append(f"{int_field} must be an integer, got: {type(val).__name__} ({val!r})")

    # Salary sanity checks (catches hallucinated values)
    sal_min = payload.get("salary_min", 0)
    sal_max = payload.get("salary_max", 0)
    if isinstance(sal_min, int) and sal_min > 500_000:
        errors.append(f"salary_min suspiciously high: {sal_min} — likely hallucinated")
    if isinstance(sal_min, int) and isinstance(sal_max, int) and sal_max < sal_min:
        errors.append(f"salary_max ({sal_max}) < salary_min ({sal_min}) — likely hallucinated")

    # Date format check
    last_date = payload.get("last_date", "")
    if last_date and not re.match(r"\d{4}-\d{2}-\d{2}T", str(last_date)):
        errors.append(f"last_date format invalid: {last_date!r} (expected ISO 8601)")

    # Slug must not contain spaces
    slug = payload.get("slug", "")
    if slug and " " in slug:
        errors.append(f"slug contains spaces: {slug!r}")

    # Enum validation
    valid_min_qual = {
        "8th_pass","10th_pass","12th_pass","iti","diploma",
        "graduate","post_graduate","engineering","medical","all"
    }
    min_qual = payload.get("min_qualification")
    if min_qual and min_qual not in valid_min_qual:
        errors.append(f"min_qualification invalid value: {min_qual!r}")

    valid_categories = {"central", "state", "public-sector"}
    category = payload.get("category")
    if category and category not in valid_categories:
        errors.append(f"category invalid value: {category!r}")

    if errors:
        raise ValueError(
            "Payload validation failed with {} error(s):\n".format(len(errors)) +
            "\n".join(f"  • {e}" for e in errors)
        )

    logger.info("    ✓ Payload validation passed")


# ─────────────────────────────────────────────
#  STEP 0 — PDF / URL EXTRACTION
# ─────────────────────────────────────────────
def extract_text_from_pdf(pdf_paths: str) -> str:
    """Extract all text from a PDF using pdfplumber."""
    text_parts = []
    with pdfplumber.open(pdf_paths) as pdf:
        for page in pdf.pages:
            t = page.extract_text()
            if t:
                text_parts.append(t)
    return "\n\n".join(text_parts)

def fetch_url_text(url: str) -> str:
    """Fetch and strip HTML from a URL."""
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    text = r.text
    if "<html" in text.lower() or "<!doctype" in text.lower():
        text = re.sub(r"<style[^>]*>[\s\S]*?</style>", "", text, flags=re.I)
        text = re.sub(r"<script[^>]*>[\s\S]*?</script>", "", text, flags=re.I)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s{2,}", " ", text).strip()
    return text


# ─────────────────────────────────────────────
#  AI — DeepSeek via OpenRouter (with retry)
# ─────────────────────────────────────────────
# def call_deepseek(prompt: str, system: str = "You are a helpful assistant.", retries: int = 3) -> str:
#     if not OPENROUTER_API_KEY:
#         raise ValueError("OPENROUTER_API_KEY is not set")

#     for attempt in range(retries):
#         try:
#             resp = requests.post(
#                 OPENROUTER_BASE,
#                 headers={
#                     "Authorization": f"Bearer {OPENROUTER_API_KEY}",
#                     "Content-Type": "application/json",
#                     "HTTP-Referer": "https://necareerhub.in",
#                     "X-Title": "NE Career Hub Job Automation",
#                 },
#                 json={
#                     "model": OPENROUTER_MODEL,
#                     "messages": [
#                         {"role": "system", "content": system},
#                         {"role": "user", "content": prompt},
#                     ],
#                     "temperature": 0.1,
#                     "max_tokens": 8192,
#                 },
#                 timeout=120,
#             )
#             resp.raise_for_status()
#             return resp.json()["choices"][0]["message"]["content"]

#         except requests.HTTPError as e:
#             status = e.response.status_code if e.response is not None else 0
#             if status == 429 or status >= 500:
#                 wait = 2 ** attempt  # 1s, 2s, 4s
#                 logger.warning(
#                     "    ⚠  API error (%s) on attempt %d/%d — retrying in %ds...",
#                     status, attempt + 1, retries, wait
#                 )
#                 time.sleep(wait)
#             else:
#                 raise  # Don't retry on 4xx client errors (bad request, auth, etc.)

#     raise RuntimeError(f"DeepSeek API failed after {retries} retries")
def call_deepseek(prompt: str, system: str = "You are a helpful assistant.", retries: int = 5) -> str:
    for attempt in range(retries):
        try:
            resp = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{GOOGLE_MODEL}:generateContent?key={GOOGLE_API_KEY}",
                headers={"Content-Type": "application/json"},
                json={
                    "system_instruction": {"parts": [{"text": system}]},
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
                # Increased backoff: 5s, 10s, 20s, 40s, 80s
                wait = (2 ** attempt) * 5 
                logger.warning("    ⚠  API error (%s) on attempt %d/%d — retrying in %ds...", status, attempt + 1, retries, wait)
                time.sleep(wait)
            else:
                raise

    raise RuntimeError(f"Gemini API failed after {retries} retries")


# ─────────────────────────────────────────────
#  SUPABASE
# ─────────────────────────────────────────────
def fetch_departments() -> list:
    if not SUPABASE_URL or not SUPABASE_KEY:
        logger.warning("    ⚠  Supabase not configured — skipping dept fetch")
        return []
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/departments?select=id,name,slug,image_url&order=name",
        headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()

def check_duplicate(content_hash_val: str) -> bool:
    """
    Check if a job with this content hash already exists in Supabase.
    Returns True if a duplicate is found.
    """
    if not SUPABASE_URL or not SUPABASE_KEY:
        return False
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}?select=id,slug&slug=ilike.*{content_hash_val}*",
            headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
            timeout=10,
        )
        r.raise_for_status()
        results = r.json()
        if results:
            logger.warning(
                "    ⚠  Possible duplicate found — existing entry with matching hash: %s",
                results[0].get("slug")
            )
            return True
    except Exception as e:
        logger.warning("    ⚠  Duplicate check failed (non-blocking): %s", e)
    return False

def publish_job(payload: dict) -> dict:
    payload.pop("isSaved", None)
    payload.pop("id", None)
    payload.pop("departmentId", None)

    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}",
        headers={
            "Content-Type": "application/json",
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Prefer": "return=representation",
        },
        json=[payload],
        timeout=15,
    )
    if not r.ok:
        logger.error("    Supabase error %s: %s", r.status_code, r.text)  # ← add this
    r.raise_for_status()
    return r.json()[0]

# ─────────────────────────────────────────────
#  PIPELINE
# ─────────────────────────────────────────────
def run_pipeline(
    pdf_paths: list = None,
    source_url: str = None,
    links: list = None,
    publish: bool = False,      # renamed from dry_run — must opt-in to publish
    output_dir: str = ".",
):
    if not pdf_paths and not source_url:
        raise ValueError("Provide either --pdf or --url")

    links = links or []
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # ── STEP 0: Fetch raw content ──────────────────
    logger.info("\n📥 Step 0: Fetching content...")
    raw_text = ""
    if source_url:
        logger.info("    Fetching URL: %s", source_url)
        raw_text = fetch_url_text(source_url)
        logger.info("    ✓ Got %d chars from URL", len(raw_text))
        
    if pdf_paths:
        all_pdf_text = []
        for pdf_file in pdf_paths:
            logger.info("    Extracting PDF: %s", pdf_file)
            pdf_text = extract_text_from_pdf(pdf_file)
            logger.info("    ✓ Extracted %d chars from %s", len(pdf_text), pdf_file)
            all_pdf_text.append(f"--- FILE: {pdf_file} ---\n{pdf_text}")
        raw_text = raw_text if raw_text else "\n\n".join(all_pdf_text)

    if not raw_text.strip():
        raise ValueError("No content extracted from source")

    # Compute content hash for idempotency
    src_hash = content_hash(raw_text)
    logger.info("    Content hash: %s", src_hash)

    # ── STEP 1: Fetch departments ─────────────────
    logger.info("\n🏛️  Step 1: Fetching departments from Supabase...")
    departments = fetch_departments()
    logger.info("    ✓ %d departments loaded", len(departments))

    # ── Set up debug output directory ─────────────
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    debug_dir = out / f"debug_{timestamp}"
    debug_dir.mkdir(exist_ok=True)

    # ── STEP 2: Extract job details ───────────────
    logger.info("\n🤖 Step 2: Extracting job details via LLM...")
    extract_input = EXTRACT_PROMPT
    if source_url:
        extract_input += f"\n\nSOURCE URL: {source_url}\n\nPAGE CONTENT:\n{raw_text}"
    else:
        extract_input += f"\n\nPDF CONTENT:\n{raw_text}"

    extracted_text = call_deepseek(extract_input)
    logger.info("    ✓ Extracted %d chars", len(extracted_text))
    (debug_dir / "2_extracted.txt").write_text(extracted_text, encoding="utf-8")

    # ── STEP 3: Dept matching ──────────────────────
    logger.info("\n🏛️  Step 3: Matching department...")
    org_name = None
    for pattern in [
        r"###\s*(?:Organization|Issuing Authority|Recruiting Body)[^\n]*\n([^\n]+)",
        r"###\s*Post Name[^\n]*\n([^\n]+)",
    ]:
        m = re.search(pattern, extracted_text, re.IGNORECASE)
        if m:
            org_name = m.group(1).strip(" *#-")
            break
    if not org_name:
        for line in extracted_text.splitlines():
            line = line.strip(" *#-")
            if line and not line.startswith("###"):
                org_name = line
                break
    org_name = org_name or "Unknown Organization"
    logger.info("    Org detected: %s", org_name)

    dept_result = {"matched": False, "suggestedName": org_name}
    resolved_dept = None
    dept_raw = ""

    if departments:
        try:
            dept_raw = call_deepseek(DEPT_MATCH_PROMPT(org_name, departments))
            dept_result = json.loads(strip_fences(dept_raw, "json"))
        except Exception as e:
            logger.warning("    ⚠  Dept match parse failed: %s", e)
    (debug_dir / "3_dept_match.txt").write_text(dept_raw, encoding="utf-8")

    if dept_result.get("matched"):
        resolved_dept = next(
            (d for d in departments if d["id"] == dept_result.get("id")), None
        ) or next(
            (d for d in departments if d["name"] == dept_result.get("name")), None
        )
        if resolved_dept:
            logger.info("    ✓ Matched dept: %s", resolved_dept["name"])
        else:
            logger.warning("    ⚠  Model said matched but dept not found in list")
    else:
        suggested = dept_result.get("suggestedName", org_name)
        logger.warning("    ⚠  No existing dept match — suggested: %s", suggested)

    # ── STEP 4: Generate TipTap HTML ──────────────
    logger.info("\n🧱 Step 4: Generating TipTap HTML...")
    html_raw = call_deepseek(HTML_PROMPT(extracted_text))
    generated_html = strip_fences(html_raw, "html")
    logger.info("    ✓ HTML generated (%d chars)", len(generated_html))
    (debug_dir / "4_html_raw.html").write_text(html_raw, encoding="utf-8")

    # ── STEP 5: Build JSON payload ─────────────────
    logger.info("\n🗂️  Step 5: Building JSON payload...")
    json_raw = call_deepseek(JSON_PROMPT(extracted_text, departments, links))
    json_clean = strip_fences(json_raw, "json")
    (debug_dir / "5_json_raw.txt").write_text(json_raw, encoding="utf-8")

    try:
        payload = json.loads(json_clean)
    except json.JSONDecodeError as e:
        raise ValueError(f"DeepSeek returned invalid JSON: {e}\n\nRaw:\n{json_clean[:500]}")

    # Merge dept info
    if resolved_dept:
        payload["departmentName"] = resolved_dept["name"]
        payload["departmentLogo"] = resolved_dept.get("image_url")
        payload["departmentId"]   = resolved_dept["id"]

    # Slug: use content hash suffix for idempotency (consistent across re-runs)
    if payload.get("slug"):
        payload["slug"] = payload["slug"] + "-" + src_hash
    
    # Inject generated HTML as description
    payload["description"] = generated_html

    logger.info("    ✓ Payload built — title: %s", payload.get("title", "???"))

    # ── STEP 5b: Validate payload ──────────────────
    logger.info("\n🔍 Step 5b: Validating payload...")
    validate_payload(payload)

    # ── SAVE OUTPUTS ───────────────────────────────
    html_file = out / f"description_{timestamp}.html"
    json_file = out / f"payload_{timestamp}.json"

    html_file.write_text(generated_html, encoding="utf-8")
    json_file.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    logger.info("\n✅ Outputs saved:")
    logger.info("   HTML   → %s", html_file)
    logger.info("   JSON   → %s", json_file)
    logger.info("   Debug  → %s/", debug_dir)

    # ── STEP 6: Publish ────────────────────────────
    if not publish:
        logger.info(
            "\n⚡ Dry run — outputs saved for review. Add --publish to push to Supabase."
        )
    else:
        if not SUPABASE_URL or not SUPABASE_KEY:
            logger.warning(
                "\n⚠  Supabase not configured — skipping publish. Set SUPABASE_URL and SUPABASE_KEY."
            )
        else:
            # Idempotency check before publishing
            if check_duplicate(src_hash):
                logger.warning(
                    "    ⚠  Duplicate detected — skipping publish. Use --force to override."
                )
            else:
                logger.info("\n🚀 Step 6: Publishing to Supabase...")
                result = publish_job(dict(payload))
                logger.info(
                    "    ✓ Posted! ID: %s · Slug: %s",
                    result.get("id"), payload.get("slug")
                )

    return payload


# ─────────────────────────────────────────────
#  CLI
# ─────────────────────────────────────────────
def parse_links(raw: list) -> list:
    """
    Accept links as 'Label::https://url' strings from CLI.
    e.g. --link "Official Notice::https://upsc.gov.in/notice.pdf"
    """
    out = []
    for item in (raw or []):
        if "::" in item:
            label, url = item.split("::", 1)
            out.append({"label": label.strip(), "url": url.strip()})
        else:
            out.append({"label": "Link", "url": item.strip()})
    return out

def main():
    global OPENROUTER_MODEL
    parser = argparse.ArgumentParser(
        description="NE Career Hub — Job Automation Pipeline (DeepSeek + pdfplumber)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""
        Examples:
          # From PDF — dry run by default (safe, saves files for review)
          python job_automation.py --pdf notification.pdf

          # From URL, preview outputs before publishing
          python job_automation.py --url https://upsc.gov.in/notice \\
            --link "Official Notice::https://upsc.gov.in/notice.pdf" \\
            --link "Apply Online::https://upsconline.nic.in/apply"

          # From PDF — actually publish to Supabase
          python job_automation.py --pdf notification.pdf --publish

          # Override keys inline
          OPENROUTER_API_KEY=sk-xxx SUPABASE_URL=https://xxx.supabase.co \\
          SUPABASE_KEY=eyJ... python job_automation.py --pdf file.pdf --publish
        """)
    )
    parser.add_argument("--pdf", action="append", help="Path to notification PDF (repeatable)")
    parser.add_argument("--url",     help="URL of job notification page")
    parser.add_argument("--link",    action="append", metavar="LABEL::URL",
                        help="Link to include (repeatable). Format: 'Label::https://...'")
    parser.add_argument("--out",     default=".", help="Output directory (default: current dir)")
    parser.add_argument("--publish", action="store_true",
                        help="Publish to Supabase (default: dry run — outputs saved for review)")
    parser.add_argument("--force",   action="store_true",
                        help="Skip duplicate check and publish anyway")
    parser.add_argument("--model",   default=OPENROUTER_MODEL,
                        help=f"OpenRouter model (default: {OPENROUTER_MODEL})")
    parser.add_argument("--from-json", metavar="FILE", help="Skip AI steps and publish an existing payload JSON directly")
    
    args = parser.parse_args()

    if args.from_json:
        with open(args.from_json) as f:
            payload = json.load(f)
        validate_payload(payload)
        result = publish_job(payload)
        logger.info("✓ Published! ID: %s · Slug: %s", result.get("id"), payload.get("slug"))
        return

    OPENROUTER_MODEL = args.model
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
        raise SystemExit(1)

if __name__ == "__main__":
    main()
