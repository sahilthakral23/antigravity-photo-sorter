"""Step 4: build the folders.  python scripts/apply.py "/where/to/put/Sorted"

Reads labels from work/labels/*.json (written by Antigravity CLI or Gemini API).
COPIES files into <output>/<Folder>/ and duplicates into <output>/_Review/duplicates/.
On a Mac (same disk) copies are APFS clones: instant and they take no extra space.
Originals are never moved, renamed or deleted. Also writes sorted.csv and duplicates.csv.
"""
import csv
import glob
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(__file__))
from common import CATEGORIES, DATA, LABELS, WORK, categories, clean_path, load, save

OVERRIDES = os.path.join(WORK, "overrides.json")
src_dir = load(os.path.join(DATA, "source.json"), {}).get("source", "")
default = (
    os.path.join(os.path.dirname(src_dir.rstrip("/\\")), os.path.basename(src_dir.rstrip("/\\")) + " (sorted)")
    if src_dir
    else ""
)
dest = clean_path(sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else default)
if not dest:
    sys.exit('Usage: python scripts/apply.py "/where/to/put/Sorted"')
if src_dir and os.path.abspath(dest).startswith(os.path.abspath(src_dir) + os.sep):
    sys.exit("Put the sorted folders outside the photos folder, or the next scan would count them twice.")

recs = load(os.path.join(DATA, "inventory.json"), [])
cats = {c["key"]: c for c in categories()}
dupes = load(os.path.join(DATA, "dupes.json"), [])

raw_labels = {}
for f in sorted(glob.glob(os.path.join(LABELS, "*.json"))):
    raw_labels.update({str(k): v for k, v in load(f, {}).items()})
if not raw_labels:
    sys.exit("No labels yet. Label the files first (Antigravity CLI: scripts/label_agy.py, or Gemini API: scripts/label_gemini_api.py).")

labels, fixed, missing = {}, 0, 0
for r in recs:
    k = str(r["id"])
    pick = raw_labels.get(k)
    if pick is None:
        missing += 1
    if pick not in cats or cats[pick]["kind"] != r["kind"]:  # photos only in photo folders, videos in video folders
        fallback = [c for c in cats.values() if c["kind"] == r["kind"]][-1]["key"]
        fixed += pick is not None
        pick = fallback
    labels[k] = pick

for k, v in load(OVERRIDES, {}).items():  # moves made by hand in the dashboard win
    if k in labels and v in cats:
        labels[k] = v

save(os.path.join(DATA, "labels.json"), labels)
src_info = load(os.path.join(DATA, "source.json"), {})
src_info["sorted"] = os.path.abspath(dest)
save(os.path.join(DATA, "source.json"), src_info)
shutil.copy(CATEGORIES, os.path.join(DATA, "categories.json"))

dupe_of = {d: g for g in dupes for d in g["dupes"]}
byid = {r["id"]: r for r in recs}


def copy(src, dst):
    if sys.platform == "darwin" and subprocess.run(["cp", "-c", "-p", src, dst], capture_output=True).returncode == 0:
        return
    shutil.copy2(src, dst)


def safe_folder_name(label):
    cleaned = label.replace("&", "and")
    for ch in ('/', '\\', ':', '*', '?', '"', '<', '>', '|'):
        cleaned = cleaned.replace(ch, "-")
    return cleaned.strip(" .") or "Sorted"


os.makedirs(dest, exist_ok=True)
old = os.path.join(dest, "sorted.csv")
if os.path.exists(old):  # rebuilding: remove only the copies this tool made last time
    with open(old, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            p = os.path.join(dest, row["file"])
            if os.path.isfile(p):
                os.remove(p)
    for d, subdirs, files in os.walk(dest, topdown=False):
        if d != dest and not os.listdir(d):
            os.rmdir(d)

with (
    open(os.path.join(dest, "sorted.csv"), "w", newline="", encoding="utf-8") as fc,
    open(os.path.join(dest, "duplicates.csv"), "w", newline="", encoding="utf-8") as fd,
):
    wc, wd = csv.writer(fc), csv.writer(fd)
    wc.writerow(["file", "folder", "original_path"])
    wd.writerow(["duplicate", "kept_copy", "type", "duplicate_path", "kept_path"])
    for n, r in enumerate(recs, 1):
        folder = safe_folder_name(cats[labels[str(r["id"])]]["label"])
        if r["id"] in dupe_of:
            g = dupe_of[r["id"]]
            k = byid[g["keep"]]
            wd.writerow([r["name"], k["name"], g["type"], r["path"], k["path"]])
            folder = os.path.join("_Review", "duplicates")
        out_dir = os.path.join(dest, folder)
        os.makedirs(out_dir, exist_ok=True)
        target = os.path.join(out_dir, r["name"])
        if os.path.exists(target):  # same name from another folder/year
            b, e = os.path.splitext(r["name"])
            target = os.path.join(out_dir, f"{b}__{r['id']}{e}")
        if not os.path.exists(target):
            copy(r["path"], target)
        wc.writerow([os.path.relpath(target, dest), folder, r["path"]])
        if n % 25 == 0 or n == len(recs):
            print(f"PROGRESS {n} {len(recs)}", flush=True)
        if n % 500 == 0:
            print(f"  copied {n}/{len(recs)}", flush=True)

counts = {}
for r in recs:
    if r["id"] not in dupe_of:
        counts[labels[str(r["id"])]] = counts.get(labels[str(r["id"])], 0) + 1
print(f"Sorted into {dest}", flush=True)
for k, c in cats.items():
    print(f"  {c['label']:24} {counts.get(k, 0)}", flush=True)
print(f"  {'_Review/duplicates':24} {len(dupe_of)}", flush=True)
if missing or fixed:
    print(
        f"  ({missing} files had no label and {fixed} had a wrong-type label; they went to the last folder of their kind)",
        flush=True,
    )
