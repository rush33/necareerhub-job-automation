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
12. Documents Required (List all documents, certificates, IDs, photos, and signatures needed for registration/application)
13. Important Dates
14. How to Apply (steps)
15. Why This Matters for NE Applicants
16. Important Notes / Hidden Conditions
17. Important Links Table (Table format with columns: Link Description | URL / Link)
18. Frequently Asked Questions (FAQs)

HARD RULES:
- NEVER skip Application Fee (default to 0 if not mentioned).
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

IMPORTANT LINKS TABLE RULES:
IMPORTANT LINKS TABLE RULES:
- Combine dynamically extracted links from the input payload with system constant links.
- Required rows:
  1. Detailed Notification / Notice (from input)
  2. Online Application Portal (from input)
  3. Recommended Books / Study Material (from input, if present)
  4. WhatsApp Channel (CONSTANT: https://whatsapp.com/channel/0029Vb7ARpvKrWR3oh1XiW1Q)
- Column 1: Resource Description (e.g., "Notice", "Apply Online", "WhatsApp Channel")
- Column 2: HTML Hyperlink formatted strictly as: <a href="URL" target="_blank" rel="noopener noreferrer">Click Here</a>

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
### Documents Required
### Important Dates
### How to Apply
### Why This Matters for NE Applicants
### Important Notes
### Important Links Table
### Frequently Asked Questions (FAQs)"""



def HTML_PROMPT(text):
    return f"""Convert this extracted job notification into clean TipTap-compatible HTML.

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
- "Selection Process", "Who Should Apply", "Documents Required", "Why This Matters for NE Applicants", and "How to Apply" MUST be unordered lists (<ul><li><p>...</p></li></ul>). Retain ✅ emojis in "Who Should Apply".

FAQS FORMATTING RULE:
- Format each Q&A pair strictly as:
  <p><strong>Q: [Question text]</strong></p>
  <p>A: [Answer text]</p>
  (Do NOT use lists or tables for FAQs).

TABLE MANDATE & STYLING:
- Transform Vacancy Details, Pay Scale, Age Relaxation, Multi-Post Qualifications, and Important Links Table into multi-column relational tables.
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

Source Document:
{text}"""

def JSON_PROMPT(text, dept_list, links):
    dept_json = json.dumps(
        [{"id": d["id"], "name": d["name"], "image_url": d.get("image_url")} for d in dept_list],
        indent=2
    )
    links_str = "\n".join(f'- Label: "{l["label"]}" → {l["url"]}' for l in links)
    return f"""You are a data extraction engine. Generate a JSON object for a Supabase jobs table.

OUTPUT: Valid JSON only. No explanation, no markdown fences, no comments.

AVAILABLE DEPARTMENTS:
{dept_json}

USER-PROVIDED LINKS:
{links_str}

STRICT FIELD RULES:
1. "title": Exact format: "[Dept] Recruitment [Year] – [Total Vacancies] [Post Name] Posts"
   - Acronym + Location: Keep specific location/state as a full word (e.g., "NIT Sikkim", "Meghalaya Health").
   - Conciseness: Use a general title like "Faculty Posts" or "Developer Posts" instead of listing all post names.
2. "overview": Insert the Editorial Quick Take here.
3. "advertisement_url" & "apply_url": Map from user-provided links.
4. "min_qualification" & "sub_category":
   - min_qualification: Lowest acceptable degree required across all posts.
   - sub_category: Determine from Posting Department and Nature of Job (e.g., "banking", "engineering", "teaching", "medical").
5. "application_fee": Integer rupees (0 if free or waived for reserved categories).
6. "last_date": Convert extracted IST last date to UTC ISO 8601 string (e.g. 2026-05-01T18:29:59+00:00).
7. "min_qualification_rank":
   0: Any | 10: 8th Pass | 20: 10th Pass | 30: 12th Pass | 32: ITI | 35: Diploma | 40: Graduate | 45: Engineering | 50: Post Graduate | 55: Medical

SCHEMA:
{{
  "slug": "kebab-base-slug-with-job-department-acronym-no-suffix",
  "title": "Org Recruitment Year – (Vacancy Number) Post Names (concise)",
  "advertisement_url": [{{"url": "...", "label": "Notice"}}],
  "apply_url": [{{"url": "...", "label": "Apply"}}],
  "category": "central | state | public-sector",
  "overview": "Editorial Quick Take",
  "last_date": "2026-05-01T18:29:59+00:00",
  "source_website": "Source website domain",
  "department_id": "matched dept id OR null",
  "min_qualification": "8th_pass | 10th_pass | 12th_pass | iti | diploma | graduate | post_graduate | engineering | medical | all",
  "state": "assam | arunachal-pradesh | meghalaya | nagaland | manipur | mizoram | tripura | sikkim | all",
  "sub_category": "psu | railway | defence | banking | teaching | police | engineering | medical | clerical | postal | administrative | upsc_ssc | judiciary | non_technical | research | healthcare | sports",
  "application_fee": 0,
  "salary_min": 0,
  "salary_max": 0,
  "keywords": ["tag1", "tag2"],
  "min_qualification_rank": 40
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
📅 Last Date: [Last Date in DD Month YYYY]
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
Last Date: {payload.get('last_date')}
Min Qualification: {payload.get('min_qualification')}
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

def run_ocr_on_pdf(pdf_path: str) -> str | None:
    """Run OCRmyPDF on a PDF file to generate an OCR'd PDF."""
    import subprocess
    import shutil
    ocrmypdf_bin = shutil.which("ocrmypdf")
    if not ocrmypdf_bin:
        logger.error("    Error: 'ocrmypdf' CLI tool not found on system PATH. "
                     "Please install it using 'brew install ocrmypdf'.")
        return None
        
    pdf_path_obj = Path(pdf_path)
    output_pdf_path = pdf_path_obj.parent / f"{pdf_path_obj.stem}_ocr{pdf_path_obj.suffix}"
    
    logger.info("    Running: ocrmypdf --force-ocr %s %s", pdf_path, output_pdf_path)
    try:
        subprocess.run(
            [ocrmypdf_bin, "--force-ocr", str(pdf_path), str(output_pdf_path)],
            capture_output=True,
            text=True,
            check=True
        )
        logger.info("    ✓ OCRmyPDF completed successfully")
        return str(output_pdf_path)
    except subprocess.CalledProcessError as e:
        logger.error("    ❌ OCRmyPDF failed with exit code %d", e.returncode)
        logger.error("    Stdout: %s", e.stdout)
        logger.error("    Stderr: %s", e.stderr)
        return None

def convert_pdf_to_md(pdf_path: str) -> str:
    """Convert a PDF file to markdown using MarkItDown."""
    import sys
    current_dir = Path(__file__).resolve().parent
    markitdown_src_path = current_dir / "markitdown" / "packages" / "markitdown" / "src"
    if markitdown_src_path.exists() and str(markitdown_src_path) not in sys.path:
        sys.path.insert(0, str(markitdown_src_path))

    from markitdown import MarkItDown
    
    md = MarkItDown()
    result = md.convert(pdf_path)
    text = result.text_content
    
    # If 0 characters are extracted, it's a scanned PDF. Run OCRmyPDF.
    if not text or not text.strip():
        logger.info("    ⚠ No text extracted. PDF might be a scanned image. Running OCRmyPDF...")
        ocr_pdf_path = run_ocr_on_pdf(pdf_path)
        if ocr_pdf_path:
            logger.info("    Converting OCR'd PDF to MD...")
            try:
                result = md.convert(ocr_pdf_path)
                text = result.text_content
            finally:
                # Clean up temporary OCR'd PDF
                try:
                    os.remove(ocr_pdf_path)
                except Exception as cleanup_err:
                    logger.warning("    Failed to delete temporary OCR PDF: %s", cleanup_err)
        else:
            logger.warning("    OCRmyPDF execution failed or was not available.")
            
    return text

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
    logger = logging.getLogger(__name__)
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GOOGLE_MODEL}:generateContent?key={GOOGLE_API_KEY}"
    for attempt in range(retries):
        try:
            resp = requests.post(
                url,
                headers={"Content-Type": "application/json"},
                json={
                    # Crucial Fix: Standard REST API uses camelCase 'systemInstruction'
                    "systemInstruction": {"parts": [{"text": system}]},
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "temperature": 0.1, 
                        "maxOutputTokens": 8192
                    },
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
            logger.info("    Converting PDF to MD via markitdown: %s", pdf_file)
            try:
                pdf_text = convert_pdf_to_md(pdf_file)
                logger.info("    ✓ Converted %d chars from %s", len(pdf_text), pdf_file)
            except Exception as e:
                logger.warning("    ⚠ MarkItDown conversion failed: %s. Falling back to pdfplumber...", e)
                pdf_text = extract_text_from_pdf(pdf_file)
                logger.info("    ✓ Extracted %d chars from %s via fallback", len(pdf_text), pdf_file)
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
    # if resolved_dept:
        # payload["departmentId"]   = resolved_dept["id"]

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

def revalidate_necareerhub(state: str, qualification: str):
    url = "https://necareerhub.in/api/revalidate"
    secret = os.environ["REVALIDATE_SECRET"]
    
    paths = [
        f"/{state}/govt-jobs",
        f"/{state}/govt-jobs/{qualification}",
        "/",
    ]
    
    for path in paths:
        requests.post(url, json={"path": path, "secret": secret})



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
