# NE Career Hub — Job Automation Pipeline

Extracts structured job data from a government notification (PDF or URL), generates TipTap HTML and a Supabase-ready JSON payload, and optionally publishes it.

---

## Setup

**Install dependencies:**

```bash
pip install requests pdfplumber python-dotenv
```

**Create a `.env` file in the project root:**

```dotenv
OPENROUTER_API_KEY=
OPENROUTER_BASE=https://openrouter.ai/api/v1/chat/completions
OPENROUTER_MODEL=deepseek/deepseek-chat

SUPABASE_URL=
SUPABASE_KEY=
SUPABASE_TABLE=jobs
```

Fill in your keys — the script reads directly from this file on every run.

---

## Recommended Workflow

```bash
# Step 1 — dry run (default), review the output files
python job_automation.py --pdf notification.pdf

# Step 2 — once reviewed, publish the saved payload directly (no AI re-run)
python job_automation.py --from-json payload_TIMESTAMP.json
```

This avoids re-running all AI steps just to publish.

---

## Usage

**From a PDF (dry run — default, safe):**

```bash
python job_automation.py --pdf notification.pdf
```

**From multiple PDFs (merged and fed as one to the LLM):**

```bash
python job_automation.py --pdf part1.pdf --pdf part2.pdf --pdf part3.pdf
```

**From a URL:**

```bash
python job_automation.py --url https://upsc.gov.in/notice
```

**With links (recommended — improves URL extraction):**

```bash
python job_automation.py --pdf notification.pdf \
  --link "Official Notice::https://upsc.gov.in/notice.pdf" \
  --link "Apply Online::https://upsconline.nic.in/apply"
```

**Publish an already-reviewed payload directly:**

```bash
python job_automation.py --from-json payload_20260430_115200.json
```

**Run and publish in one shot (skip review):**

```bash
python job_automation.py --pdf notification.pdf --publish
```

---

## Flags

| Flag          | Default  | Description                                                 |
| ------------- | -------- | ----------------------------------------------------------- |
| `--pdf`       | —        | Path to notification PDF                                    |
| `--url`       | —        | URL of notification page                                    |
| `--link`      | —        | `Label::URL` pairs, repeatable                              |
| `--out`       | `.`      | Output directory                                            |
| `--publish`   | off      | Push to Supabase after AI steps (default is dry run)        |
| `--from-json` | —        | Skip AI steps and publish an existing payload JSON directly |
| `--force`     | off      | Skip duplicate check when publishing                        |
| `--model`     | deepseek | Override the OpenRouter model                               |

---

## Output

Each run produces files in your output directory:

```
description_TIMESTAMP.html   ← TipTap-ready HTML
payload_TIMESTAMP.json       ← Supabase payload (review before publishing)
debug_TIMESTAMP/
  ├── 2_extracted.txt        ← Raw AI extraction output
  ├── 3_dept_match.txt       ← Raw dept matching output
  ├── 4_html_raw.html        ← Raw HTML before fence stripping
  └── 5_json_raw.txt         ← Raw JSON before parsing
pipeline.log                 ← Full run history with timestamps
```

**Always review `payload_TIMESTAMP.json` before publishing.**
