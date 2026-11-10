import json


# ==========================================
# CONFIGURATION
# ==========================================

MODE = "html_to_json"
# Options:
# "html_to_json"
# "json_to_html"

INPUT_FILE = "input.txt"
OUTPUT_FILE = "output.txt"


# ==========================================
# CONVERSION
# ==========================================

with open(INPUT_FILE, "r", encoding="utf-8") as file:
    content = file.read()


if MODE == "html_to_json":
    # Normal HTML → JSON-safe single string
    result = json.dumps(content, ensure_ascii=False)

elif MODE == "json_to_html":
    # JSON-safe string → Normal HTML
    result = json.loads(content)

else:
    raise ValueError(
        "Invalid MODE. Use 'html_to_json' or 'json_to_html'."
    )


# ==========================================
# SAVE OUTPUT
# ==========================================

with open(OUTPUT_FILE, "w", encoding="utf-8") as file:
    file.write(result)


print(f"Done! {MODE}")
print(f"Output saved to: {OUTPUT_FILE}")