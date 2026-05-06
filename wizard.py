#!/usr/bin/env python3
"""
NE Career Hub — Interactive Job Automation Wizard
Wraps job_automation.py with a step-by-step terminal prompt.
"""

import os
import sys
import json
import subprocess
import tempfile
import textwrap
from pathlib import Path
from glob import glob

# ─────────────────────────────────────────────
#  COLOURS
# ─────────────────────────────────────────────
R  = "\033[0m"        # reset
B  = "\033[1m"        # bold
CY = "\033[96m"       # cyan
GR = "\033[92m"       # green
YL = "\033[93m"       # yellow
RD = "\033[91m"       # red
DM = "\033[2m"        # dim


def hr(char="─", width=52):
    print(DM + char * width + R)

def header(text):
    print()
    hr()
    print(f"{B}{CY}  {text}{R}")
    hr()

def ask(prompt, default=None):
    """Simple input with optional default."""
    hint = f" {DM}[{default}]{R}" if default else ""
    val = input(f"  {B}{prompt}{R}{hint}: ").strip()
    return val if val else default

def confirm(prompt, default="y"):
    """y/n prompt. Returns bool."""
    hint = "Y/n" if default == "y" else "y/N"
    val = input(f"  {B}{prompt}{R} {DM}({hint}){R}: ").strip().lower()
    if not val:
        return default == "y"
    return val in ("y", "yes")

def success(msg):
    print(f"  {GR}✓{R}  {msg}")

def warn(msg):
    print(f"  {YL}⚠{R}  {msg}")

def error(msg):
    print(f"  {RD}✗{R}  {msg}")

def info(msg):
    print(f"  {DM}{msg}{R}")


# ─────────────────────────────────────────────
#  STEP COLLECTORS
# ─────────────────────────────────────────────
def collect_pdfs():
    header("📄  PDF Files")
    pdfs = []
    while True:
        path = ask("PDF path (drag & drop or type)").strip("'\" ")
        if not path:
            if not pdfs:
                error("At least one PDF is required.")
                continue
            break
        p = Path(path)
        if not p.exists():
            error(f"File not found: {path}")
            continue
        pdfs.append(str(p))
        success(f"Added: {p.name}")
        if not confirm("Add another PDF?", default="n"):
            break
    return pdfs


def collect_links(kind):
    """Collect labelled links for 'Advertisement' or 'Apply'."""
    emoji = "🔗" if kind == "Advertisement" else "✅"
    header(f"{emoji}  {kind} Links")
    info("Press Enter with no URL to skip.")
    links = []
    while True:
        url = ask("URL (or Enter to skip)").strip("'\" ")
        if not url:
            break
        label = ask("Label", default=kind)
        links.append({"label": label, "url": url})
        success(f"Added: {label}")
        if not confirm("Add another?", default="n"):
            break
    return links


# ─────────────────────────────────────────────
#  FIND LATEST OUTPUT FILES
# ─────────────────────────────────────────────
def find_latest(pattern):
    files = sorted(glob(pattern), reverse=True)
    return files[0] if files else None


# ─────────────────────────────────────────────
#  PAYLOAD PREVIEW
# ─────────────────────────────────────────────
def preview_payload(payload: dict):
    header("📋  Payload Preview")
    fields = [
        ("Title",     payload.get("title", "—")),
        ("Slug",      payload.get("slug",  "—")),
        ("Last Date", payload.get("lastDate", "—")),
        ("Category",  payload.get("category", "—")),
        ("Dept",      payload.get("departmentName", "—")),
        ("Salary",    f"₹{payload.get('salaryMin',0):,} – ₹{payload.get('salaryMax',0):,}/month"),
        ("Fee",       f"₹{payload.get('applicationFee', 0)}"),
        ("Min Qual",  payload.get("minQualification", "—")),
        ("State",     payload.get("state", "—")),
    ]
    for label, value in fields:
        print(f"  {B}{label:<12}{R} {value}")


# ─────────────────────────────────────────────
#  OPEN EDITOR
# ─────────────────────────────────────────────
def open_editor(filepath: str) -> dict:
    """Open payload JSON in system editor. Returns updated payload."""
    editor = os.environ.get("EDITOR", "nano")  # falls back to nano
    print(f"\n  {DM}Opening {filepath} in VS Code... Save and close the tab, then press Enter here to continue.{R}\n")
    subprocess.Popen(["code", "--wait", filepath])
    input("  Press Enter when done editing... ")
    with open(filepath, encoding="utf-8") as f:
        return json.load(f)


# ─────────────────────────────────────────────
#  PUBLISH via job_automation.py --from-json
# ─────────────────────────────────────────────
def publish(payload_path: str):
    header("🚀  Publishing to Supabase")
    result = subprocess.run(
        [sys.executable, "job_automation.py", "--from-json", payload_path],
        text=True,
    )
    return result.returncode == 0


# ─────────────────────────────────────────────
#  MAIN WIZARD
# ─────────────────────────────────────────────
def main():
    os.system("clear")
    print(f"\n{B}{CY}  🚀  NE Career Hub — Job Automation Wizard{R}")
    hr("═")
    print(f"  {DM}This wizard guides you through PDF extraction,")
    print(f"  payload review, and Supabase publishing.{R}\n")

    # ── Collect inputs ────────────────────────
    pdfs    = collect_pdfs()
    ad_links    = collect_links("Notice")
    apply_links = collect_links("Apply")

    # ── Build command ─────────────────────────
    header("⚙️   Running Pipeline")
    cmd = [sys.executable, "job_automation.py"]
    for p in pdfs:
        cmd += ["--pdf", p]
    for l in ad_links + apply_links:
        cmd += ["--link", f"{l['label']}::{l['url']}"]

    print(f"  {DM}Command: {' '.join(cmd)}{R}\n")

    result = subprocess.run(cmd, text=True)

    if result.returncode != 0:
        error("Pipeline failed — check logs above.")
        sys.exit(1)

    # ── Find generated payload ────────────────
    payload_path = find_latest("payload_*.json")
    if not payload_path:
        error("Could not find generated payload JSON.")
        sys.exit(1)

    success(f"Payload saved: {payload_path}")

    with open(payload_path, encoding="utf-8") as f:
        payload = json.load(f)

    # ── Preview ───────────────────────────────
    preview_payload(payload)

    # ── Edit? ─────────────────────────────────
    print()
    if confirm("✏️   Edit payload before publishing?", default="n"):
        payload = open_editor(payload_path)
        # Save edited version back
        with open(payload_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        success("Payload updated.")
        preview_payload(payload)

    # ── Publish? ──────────────────────────────
    print()
    if confirm("🚀  Publish to Supabase?", default="n"):
        ok = publish(payload_path)
    if ok:
        print()
        success("Job published successfully!")

    # ── Telegram notification ─────────────────
        header("📣  Telegram Notification")
        from job_automation import format_telegram_message, send_telegram

        tg_message = format_telegram_message(payload)
        print(f"\n{DM}  Preview:{R}\n")
        print(textwrap.indent(tg_message, "    "))
        print()

        if confirm("Send this to Telegram?", default="y"):
            sent = send_telegram(tg_message)
            if sent:
                success("Telegram message sent!")
            else:
                error("Telegram send failed — check logs.")

        if confirm("🧹  Clean up all generated files and PDFs?", default="y"):
            import glob as _glob
            to_delete = (
                pdfs
                + _glob.glob("payload_*.json")
                + _glob.glob("description_*.html")
                + [str(p) for p in Path(".").glob("debug_*/")]
            )
            for item in to_delete:
                item_path = Path(item)
                if item_path.is_dir():
                    import shutil
                    shutil.rmtree(item_path, ignore_errors=True)
                elif item_path.exists():
                    item_path.unlink()
            success("Cleaned up.")
        else:
            error("Publish failed — check logs above.")
    else:
        warn("Skipped publishing. Run this to publish later:")
        print(f"\n  {CY}python job_automation.py --from-json {payload_path}{R}\n")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n\n  {YL}Cancelled.{R}\n")
        sys.exit(0)
