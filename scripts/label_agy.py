"""Label every file with Antigravity CLI (`agy`) running headless in the background.

  python scripts/label_agy.py                # all batches (resumes where it stopped)
  python scripts/label_agy.py --only 1       # just the first batch, to test
  python scripts/label_agy.py --model <slug> # override model for this run

Uses the `agy` command you already have (your Antigravity login, no API key needed).
Contact sheets of 30 previews are grouped 3 per batch (about 90 files) and up to 3
batches run at once. Each batch's answer is saved to work/labels/agy_NN.json,
so the dashboard can show progress and fly those photos into their folders.
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(__file__))
from common import DATA, LABELS, WORK, categories, find_agy, load, save

SHEETS_PER_BATCH = 3
WORKERS = int(os.environ.get("AGY_WORKERS", 3))

ap = argparse.ArgumentParser()
ap.add_argument("--only", type=int, default=0, help="only run the first N batches")
ap.add_argument("--model", default=os.environ.get("AGY_MODEL", "").strip(), help="optional agy model slug")
args = ap.parse_args()

agy = find_agy()
if not agy:
    sys.exit(
        "The `agy` command (Antigravity CLI) was not found. Install Antigravity CLI, "
        "or switch to Gemini API under Advanced."
    )

sheet_dir = os.path.join(WORK, "sheets")
if not glob.glob(os.path.join(sheet_dir, "sheet_*.jpg")):
    subprocess.run([sys.executable, os.path.join(os.path.dirname(__file__), "sheets.py")], check=True)
sheets = sorted(glob.glob(os.path.join(sheet_dir, "sheet_*.jpg")))
inv = load(os.path.join(DATA, "inventory.json"), [])
cats = categories()
valid_keys = {c["key"] for c in cats}
os.makedirs(LABELS, exist_ok=True)

SCHEMA_PATH = os.path.join(WORK, "label_schema.json")
SCHEMA = {
    "type": "object",
    "properties": {
        "labels": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "key": {"type": "string"},
                },
                "required": ["id", "key"],
            },
        }
    },
    "required": ["labels"],
}
save(SCHEMA_PATH, SCHEMA)

folders = "\n".join(f"- {c['key']} ({c['kind']}): {c['rule']}" for c in cats)
batches = [sheets[i:i + SHEETS_PER_BATCH] for i in range(0, len(sheets), SHEETS_PER_BATCH)]
if args.only:
    batches = batches[:args.only]


def ids_in(batch):
    first = int(re.search(r"sheet_(\d+)", os.path.basename(batch[0])).group(1)) * 30
    last = (int(re.search(r"sheet_(\d+)", os.path.basename(batch[-1])).group(1)) + 1) * 30
    return [str(r["id"]) for r in inv[first:last]]


def extract_labels(res, want_set):
    out = {}
    so = res.get("structured_output")
    if isinstance(so, dict) and isinstance(so.get("labels"), list):
        for item in so["labels"]:
            if isinstance(item, dict):
                k, v = str(item.get("id", "")).strip(), str(item.get("key", "")).strip()
                if k in want_set and v in valid_keys:
                    out[k] = v
        if out:
            return out

    text = str(res.get("response") or res.get("result") or "")
    # Try parsing structured {"labels": [...]} or flat {"12": "food"} from text
    for blob in re.findall(r"\{[\s\S]*\}", text):
        try:
            obj = json.loads(blob)
        except ValueError:
            continue
        if isinstance(obj, dict):
            if isinstance(obj.get("labels"), list):
                for item in obj["labels"]:
                    if isinstance(item, dict):
                        k, v = str(item.get("id", "")).strip(), str(item.get("key", "")).strip()
                        if k in want_set and v in valid_keys:
                            out[k] = v
            else:
                for k, v in obj.items():
                    ks, vs = str(k).strip(), str(v).strip()
                    if ks in want_set and vs in valid_keys:
                        out[ks] = vs
        if out:
            return out
    return out


def run_batch(n, batch):
    out_file = os.path.join(LABELS, f"agy_{n:02d}.json")
    want = ids_in(batch)
    want_set = set(want)
    if os.path.exists(out_file) and want_set <= set(load(out_file, {})):
        return n, len(want), 0, "cached"

    abs_paths = [os.path.abspath(p) for p in batch]
    prompt = (
        "You sort a camera roll into folders. These are the folders, as key (kind): what goes in it:\n"
        f"{folders}\n\n"
        "Use read_file to inspect each of these contact sheet images:\n"
        + "\n".join(abs_paths)
        + "\n\n"
        "Each sheet shows up to 30 square previews, each tagged in yellow with a number (its id). "
        "A tag with VIDEO is a video and MUST get a (video) folder key. Every other tile is a photo "
        "and MUST get a (photo) folder key. PNG usually means a screenshot. "
        f"Assign every single id in this batch ({want[0]}..{want[-1]}, {len(want)} total) exactly one folder key from the list.\n\n"
        'Return JSON matching the schema: {"labels": [{"id": "12", "key": "food"}, ...]}.'
    )

    cmd = [
        agy,
        "-p", prompt,
        "--output-format", "json",
        "--json-schema", os.path.abspath(SCHEMA_PATH),
        "--disable-slash-commands",
        "--print-timeout", "15m",
        "--add-dir", os.path.abspath(WORK),
    ]
    if args.model:
        cmd.extend(["--model", args.model])

    t0, why = time.time(), ""
    for attempt in range(4):
        if attempt:
            time.sleep(8 * attempt)
        try:
            r = subprocess.run(
                cmd,
                cwd=WORK,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=920,
            )
        except subprocess.TimeoutExpired:
            why = "took longer than 15 minutes"
            continue

        raw_out = (r.stdout or "").strip()
        try:
            res = json.loads(raw_out) if raw_out else {"response": r.stderr or ""}
        except ValueError:
            # Handle any stray leading log lines before the final JSON line
            res = {"response": raw_out or r.stderr or ""}
            for line in reversed(raw_out.splitlines()):
                line = line.strip()
                if line.startswith("{") and line.endswith("}"):
                    try:
                        res = json.loads(line)
                        break
                    except ValueError:
                        pass

        labels = extract_labels(res, want_set)
        if len(labels) >= 0.9 * len(want):
            save(out_file, labels)
            tok = (res.get("usage") or {}).get("total_tokens", 0) or 0
            how = f"{time.time() - t0:.0f}s" + (f", try {attempt + 1}" if attempt else "")
            return n, len(labels), tok, how

        why = (
            str(res.get("error") or "").strip()[:160]
            or " ".join(str(res.get("response") or "").split())[:160]
            or (r.stderr or "").strip()[:160]
            or "empty answer"
        )
        with open(os.path.join(WORK, f"agy_failed_{n:02d}.txt"), "w", encoding="utf-8") as f:
            f.write(f"EXIT={r.returncode}\nSTDOUT:\n{r.stdout}\n\nSTDERR:\n{r.stderr}\n")
        print(f"  batch {n + 1}: try {attempt + 1} gave {len(labels)}/{len(want)} labels ({why}), retrying", flush=True)

    raise RuntimeError(f"batch {n + 1} failed after 4 tries: {why}")


model_note = args.model or "default agy model"
print(
    f"Antigravity CLI is labelling {len(inv)} files in {len(batches)} batches ({WORKERS} at a time, {model_note})...",
    flush=True,
)
t0, done, tokens, failed = time.time(), 0, 0, []
with ThreadPoolExecutor(WORKERS) as ex:
    futs = [ex.submit(run_batch, n, b) for n, b in enumerate(batches)]
    for f in as_completed(futs):
        try:
            n, k, tok, how = f.result()
            done += 1
            tokens += tok
            print(f"PROGRESS {done} {len(batches)}", flush=True)
            print(f"  batch {done}/{len(batches)} done: {k} files ({how})", flush=True)
        except Exception as e:
            failed.append(str(e))
            print(f"  {e}", flush=True)

labelled = set()
for f in glob.glob(os.path.join(LABELS, "*.json")):
    labelled |= set(load(f, {}))
missing = len(inv) - len(labelled & {str(r["id"]) for r in inv})
print(
    f"Done in {time.time() - t0:.0f}s: {len(inv) - missing} labelled, {missing} missing"
    + (f", {tokens:,} tokens" if tokens else "")
    + (f", {len(failed)} batches failed (run again to retry)" if failed else ""),
    flush=True,
)
sys.exit(1 if failed else 0)
