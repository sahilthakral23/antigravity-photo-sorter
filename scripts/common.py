"""Shared paths and helpers. Everything the tool writes lives inside this repo:
  dashboard/data/   inventory, thumbnails, labels (what the dashboard reads)
  work/             contact sheets and raw labels (scratch)
Your original photos are only ever read, never changed.
"""
import json
import os
import re
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "dashboard", "data")
THUMBS = os.path.join(DATA, "thumbs")
WORK = os.path.join(ROOT, "work")
LABELS = os.path.join(WORK, "labels")  # Antigravity CLI / Gemini API write one JSON per batch here
CATEGORIES = os.path.join(ROOT, "categories.json")
ENV_FILE = os.path.join(ROOT, ".env")

IMG = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
VID = {".mov", ".mp4", ".m4v", ".3gp", ".avi", ".mkv", ".webm"}


def load(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1, ensure_ascii=False)


def categories():
    cats = load(CATEGORIES)
    keys = [c["key"] for c in cats]
    assert len(keys) == len(set(keys)), "categories.json has duplicate keys"
    for kind in ("photo", "video"):
        assert any(c["kind"] == kind for c in cats), f"categories.json needs at least one {kind} folder"
    return cats


def find_agy():
    """Locate the Antigravity CLI (`agy`) binary on PATH or common install locations."""
    found = shutil.which("agy")
    if found:
        return found
    home = os.path.expanduser("~")
    candidates = []
    if sys.platform.startswith("win"):
        local_app = os.environ.get("LOCALAPPDATA", os.path.join(home, "AppData", "Local"))
        candidates = [
            os.path.join(local_app, "agy", "bin", "agy.exe"),
            os.path.join(home, ".local", "bin", "agy.exe"),
        ]
    else:
        candidates = [
            os.path.join(home, ".local", "bin", "agy"),
            "/usr/local/bin/agy",
            "/opt/homebrew/bin/agy",
        ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


def get_gemini_key():
    """Read GEMINI_API_KEY (or GOOGLE_API_KEY) from environment or local .env file."""
    for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        val = (os.environ.get(name) or "").strip()
        if val:
            return val
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE, encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith("#"):
                    continue
                k, _, v = s.partition("=")
                if k.strip() in ("GEMINI_API_KEY", "GOOGLE_API_KEY") and v.strip():
                    return v.strip().strip('"').strip("'")
    return ""


def save_gemini_key(key):
    """Persist GEMINI_API_KEY in local .env file without clobbering other variables."""
    key = key.strip()
    lines = []
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE, encoding="utf-8") as f:
            lines = f.read().splitlines()
    out = [l for l in lines if not re.match(r"^\s*GEMINI_API_KEY\s*=", l)]
    if key:
        out.append(f"GEMINI_API_KEY={key}")
    with open(ENV_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + ("\n" if out else ""))


def clean_path(p):
    """Normalize a user-provided folder path without breaking Windows drive roots."""
    p = os.path.expanduser((p or "").strip().strip('"').strip("'"))
    if not p:
        return ""
    if sys.platform.startswith("win") and re.match(r"^[A-Za-z]:[\\/]?$", p):
        return p[:2] + "\\"
    return p.rstrip("/\\")
