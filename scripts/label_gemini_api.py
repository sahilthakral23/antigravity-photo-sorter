"""Backup labeler: label every file directly via the Gemini API using contact sheets.

  python scripts/label_gemini_api.py --estimate            # no API calls: estimate batches & tokens
  python scripts/label_gemini_api.py                       # label everything -> work/labels/api_NN.json
  python scripts/label_gemini_api.py --model gemini-2.5-flash-lite

Needs GEMINI_API_KEY (or GOOGLE_API_KEY) in your environment or in a .env file in this repo.
Sends 3 contact sheets (~90 small 222px previews) per request, never the original photos.
"""
import argparse
import base64
import glob
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(__file__))
from common import DATA, LABELS, WORK, categories, get_gemini_key, load, save

SHEETS_PER_BATCH = 3
WORKERS = int(os.environ.get("GEMINI_WORKERS", 4))
TOKENS_PER_SHEET = 1200  # ~258 image tokens + prompt share + ~350 JSON output tokens

ap = argparse.ArgumentParser()
ap.add_argument("--estimate", action="store_true", help="estimate tokens without making API calls")
ap.add_argument("--only", type=int, default=0, help="only run the first N batches")
ap.add_argument("--model", default=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash").strip() or "gemini-2.5-flash")
args = ap.parse_args()

inv = load(os.path.join(DATA, "inventory.json"), [])
sheet_dir = os.path.join(WORK, "sheets")
if not glob.glob(os.path.join(sheet_dir, "sheet_*.jpg")):
    subprocess.run([sys.executable, os.path.join(os.path.dirname(__file__), "sheets.py")], check=True)
sheets = sorted(glob.glob(os.path.join(sheet_dir, "sheet_*.jpg")))
batches = [sheets[i:i + SHEETS_PER_BATCH] for i in range(0, len(sheets), SHEETS_PER_BATCH)]
if args.only:
    batches = batches[:args.only]

if args.estimate:
    est_tokens = len(sheets) * TOKENS_PER_SHEET
    print(f"{len(inv)} files across {len(sheets)} contact sheets ({len(batches)} Gemini API calls, model {args.model}).")
    print(f"Estimated tokens: ~{est_tokens:,} total (~{max(1, est_tokens // max(1, len(inv)))} tokens per file).")
    sys.exit(0)

KEY = get_gemini_key()
if not KEY:
    sys.exit("No Gemini API key found. Add GEMINI_API_KEY=your-key in .env or enter it under Advanced in the dashboard.")

cats = categories()
valid_keys = {c["key"] for c in cats}
os.makedirs(LABELS, exist_ok=True)
folders = "\n".join(f"- {c['key']} ({c['kind']}): {c['rule']}" for c in cats)

RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "labels": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "id": {"type": "STRING"},
                    "key": {"type": "STRING"},
                },
                "required": ["id", "key"],
            },
        }
    },
    "required": ["labels"],
}


def ids_in(batch):
    first = int(re.search(r"sheet_(\d+)", os.path.basename(batch[0])).group(1)) * 30
    last = (int(re.search(r"sheet_(\d+)", os.path.basename(batch[-1])).group(1)) + 1) * 30
    return [str(r["id"]) for r in inv[first:last]]


def run_batch(n, batch):
    out_file = os.path.join(LABELS, f"api_{n:02d}.json")
    want = ids_in(batch)
    want_set = set(want)
    if os.path.exists(out_file) and want_set <= set(load(out_file, {})):
        return n, len(want), 0, "cached"

    parts = []
    for p in batch:
        with open(p, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        parts.append({"inlineData": {"mimeType": "image/jpeg", "data": b64}})

    prompt = (
        "You sort a camera roll into folders. These are the folders, as key (kind): what goes in it:\n"
        f"{folders}\n\n"
        "Each contact sheet image above shows up to 30 square previews, each tagged in yellow with its numeric id. "
        "A tag containing VIDEO is a video and MUST receive a (video) folder key. Every other tile is a photo "
        "and MUST receive a (photo) folder key. PNG usually means a screenshot. "
        f"Classify every single id in this batch ({want[0]}..{want[-1]}, {len(want)} items total) into exactly one folder key.\n"
        'Return JSON: {"labels": [{"id": "12", "key": "food"}, ...]}.'
    )
    parts.append({"text": prompt})

    body = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            "temperature": 0.1,
            "responseMimeType": "application/json",
            "responseSchema": RESPONSE_SCHEMA,
        },
    }
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{args.model}:generateContent"
    data = json.dumps(body).encode("utf-8")

    t0, why = time.time(), ""
    for attempt in range(5):
        if attempt:
            time.sleep(2 ** attempt * 2)
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": KEY,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                j = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")[:300]
            why = f"HTTP {e.code}: {err_body}"
            if e.code in (400, 401, 403):
                raise RuntimeError(f"Gemini API rejected the request ({why})")
            continue
        except Exception as e:
            why = str(e)
            continue

        text = ""
        try:
            text = j["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError):
            why = f"unexpected response: {str(j)[:160]}"
            continue

        labels = {}
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict) and isinstance(parsed.get("labels"), list):
                for item in parsed["labels"]:
                    k, v = str(item.get("id", "")).strip(), str(item.get("key", "")).strip()
                    if k in want_set and v in valid_keys:
                        labels[k] = v
            elif isinstance(parsed, dict):
                for k, v in parsed.items():
                    ks, vs = str(k).strip(), str(v).strip()
                    if ks in want_set and vs in valid_keys:
                        labels[ks] = vs
        except ValueError as e:
            why = f"invalid JSON: {e}"
            continue

        if len(labels) >= 0.9 * len(want):
            save(out_file, labels)
            tok = (j.get("usageMetadata") or {}).get("totalTokenCount", 0) or 0
            how = f"{time.time() - t0:.1f}s" + (f", try {attempt + 1}" if attempt else "")
            return n, len(labels), tok, how

        why = f"only {len(labels)}/{len(want)} valid labels returned"

    raise RuntimeError(f"batch {n + 1} failed after 5 tries: {why}")


print(f"Gemini API ({args.model}) is labelling {len(inv)} files in {len(batches)} batches ({WORKERS} at a time)...", flush=True)
t0, done, tokens, failed = time.time(), 0, 0, []
with ThreadPoolExecutor(WORKERS) as ex:
    futs = [ex.submit(run_batch, n, b) for n, b in enumerate(batches)]
    for f in as_completed(futs):
        try:
            n, k, tok, how = f.result()
            done += 1
            tokens += tok
            print(f"PROGRESS {done} {len(batches)}", flush=True)
            print(f"  batch {done}/{len(batches)} done: {k} files ({how}, {tok:,} tokens)", flush=True)
        except Exception as e:
            failed.append(str(e))
            print(f"  {e}", flush=True)

labelled = set()
for f in glob.glob(os.path.join(LABELS, "*.json")):
    labelled |= set(load(f, {}))
missing = len(inv) - len(labelled & {str(r["id"]) for r in inv})
print(
    f"Done in {time.time() - t0:.1f}s: {len(inv) - missing} labelled, {missing} missing, {tokens:,} tokens"
    + (f", {len(failed)} batches failed (run again to retry)" if failed else ""),
    flush=True,
)
sys.exit(1 if failed else 0)
