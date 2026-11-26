# Gemini Photo Sorter

Point Gemini (via **Antigravity CLI**) at your camera roll. It looks at every photo and video, sorts them into folders, detects duplicates, and animates every photo flying into its folder on a live 3D localhost dashboard.

Inspired by [claude-photo-sorter](https://github.com/kairothq/claude-photo-sorter), built in Python for **Antigravity CLI (`agy`)** with **Gemini API** as an optional backup and first-class Windows, macOS, and Linux support.

---

## Features

- **Smart photo & video categorization:** Sorts media into 14 default folders (*Selfies, People & family, Pets & animals, Food & drinks, Travel & nature, City & nights out, Home & everyday, Documents & receipts, Memes & saved posts, Other screenshots, Talking & selfie videos, Travel clips, Screen recordings, Everyday clips*) — or create and edit your own folders and plain-English sorting rules in the UI.
- **Exact & near-duplicate detection:** Spots byte-identical copies (SHA-256) and burst/resized look-alikes (perceptual hashing with a PNG screenshot pixel-diff guard). Keeps the largest copy and places extras in `_Review/duplicates/` along with `duplicates.csv`.
- **100% safe & read-only on originals:** Never moves, renames, or deletes your source files. Writes sorted copies (instant APFS clones on macOS, safe copies on Windows/Linux) plus `sorted.csv` and `duplicates.csv`.
- **Antigravity CLI default + Gemini API backup:** Uses your local `agy` login out of the box with no API key required, or switch to the Gemini API (`gemini-2.5-flash` / `gemini-2.5-flash-lite` / `gemini-2.5-pro`) under **Advanced** with an in-app key saver.
- **Built-in Folder Browser & Live 3D Dashboard:** Runs locally on `http://127.0.0.1:8765` with an in-browser folder picker (Quick Access, drives, new folder creation), rotating 3D thumbnail sphere, live flying-card sorting, full-size lightbox with keyboard navigation, manual "Move to" overrides, light/dark themes, and replay mode (`Space`).

---

## Quick Start

### Windows
1. Double-click **`Open Photo Sorter.bat`** (or run `start.bat` in a terminal).
   - First run automatically creates `.venv` and installs `pillow`, `pillow-heif`, and `imagehash`.
   - Preview the animated dashboard without using your own photos:
     ```bat
     start.bat --demo
     ```
2. Open **http://127.0.0.1:8765** in your browser.
3. Click **Choose folder**, select your photos folder, pick where sorted copies should go, and click **Start sorting**.

### macOS & Linux
```bash
./setup.sh          # once: creates .venv/ and installs Python packages
./start.sh          # starts server and opens http://127.0.0.1:8765
./start.sh --demo   # try the 1,057-file synthetic demo camera roll
```

<details>
<summary>How to export your photos first</summary>

- **Google Photos:** Go to [takeout.google.com](https://takeout.google.com), select **Google Photos**, download, and unzip into one folder (Takeout `.json` date/GPS sidecars are read automatically).
- **Apple Photos (Mac):** Select your photos, then **File → Export → Export Unmodified Originals** into a new folder.
</details>

---

## Labeling Engines

| | Antigravity CLI (`agy`, default) | Gemini API (Advanced backup) |
|---|---|---|
| **How it works** | Runs headless `agy -p` with strict JSON schema on 3 contact sheets (~90 previews) per batch | Sends 3 contact sheets (~90 previews) to Gemini `generateContent` with structured JSON output |
| **Setup** | Uses your existing `agy` login — no API key needed | Paste a Google AI Studio `GEMINI_API_KEY` under **Advanced** (saved locally to `.env`) |
| **Models** | Your default `agy` model, or choose Flash / Neon under **Advanced** | `gemini-2.5-flash` (default), `gemini-2.5-flash-lite`, or `gemini-2.5-pro` |
| **Resume support** | Cached per batch in `work/labels/` — interrupted runs resume cleanly | Cached per batch in `work/labels/` — interrupted runs resume cleanly |

---

## Dashboard Controls

| Control | What it does |
|---|---|
| **Sort with Gemini** | Starts sorting during setup; replays the 3D flying animation after sorting (`?d=20` slows replay for recording; `Space` also replays). |
| **Choose folder** | Opens the in-browser folder picker with Quick Access (Pictures, Desktop, Downloads, OneDrive, Drives) and path bar. |
| **Advanced** | Add/remove folders, switch between **Antigravity CLI** and **Gemini API**, choose a model, save an API key, or test with **200 random files** first. |
| **Click any folder card** | Browse thumbnails inside that folder; click any photo/video for the full-size lightbox (`←`/`→` to flip, **Move to** to override its folder, **Show original** to reveal on disk). |
| **Edit folders** | Customize emojis, names, photo/video kind, and the plain-English rules Gemini reads. |
| **Open sorted folder** | Opens the output directory in Explorer / Finder. |
| **☀ / ☾** | Toggle light and dark mode. |

---

## Privacy & Security

- Binds strictly to `127.0.0.1` (unreachable from other devices on your network) and verifies `Origin` headers on all `POST` requests.
- Full-resolution originals **never** leave your machine — only small 222px contact-sheet thumbnails are inspected for classification.
- Local runtime artifacts (`dashboard/data/`, `dashboard/peek/`, `dashboard/demo/`, `work/`, `.env`, `.venv/`) are all excluded via `.gitignore`.

---

## Running Pipeline Steps Manually

```powershell
# 1. Scan photos (add --limit 200 to test a sample first)
.venv\Scripts\python.exe scripts\scan.py "C:\Path\To\Photos"

# 2. Detect exact & near duplicates (visual preview: work\duplicates.jpg)
.venv\Scripts\python.exe scripts\dupes.py

# 3. Label via Antigravity CLI (default) or Gemini API (backup)
.venv\Scripts\python.exe scripts\label_agy.py
# or:
.venv\Scripts\python.exe scripts\label_gemini_api.py --estimate
.venv\Scripts\python.exe scripts\label_gemini_api.py

# 4. Copy into sorted folders + write sorted.csv and duplicates.csv
.venv\Scripts\python.exe scripts\apply.py "C:\Path\To\Photos (sorted)"
```
*(On macOS/Linux, replace `.venv\Scripts\python.exe` with `.venv/bin/python`.)*

---

## Troubleshooting

- **Port 8765 is already in use:** Set `PORT=8766` before running `start.bat` / `./start.sh`.
- **`agy` command not found:** Install and authenticate Antigravity CLI once (`agy`), or switch to **Gemini API (Backup)** under **Advanced**.
- **Videos show grey placeholder previews:** Install `ffmpeg` (`winget install Gyan.FFmpeg` on Windows, `brew install ffmpeg` on macOS).
- **A batch failed mid-sort:** Click **Start sorting** again — finished batches in `work/labels/` are cached and automatically skipped.

---

## Documentation for Developers & AI Agents

- **[`AGENTS.md`](AGENTS.md)** — Agent ground rules, step-by-step modification playbooks, known Windows/Gemini pitfalls, and verification checklist.
- **[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)** — Full system diagram, file-by-file module reference, HTTP API contract, disk data schemas, and frontend animation/state-machine internals.

## Requirements

- Python 3.9+ (tested on Python 3.12)
- [Antigravity CLI (`agy`)](https://antigravity.google) (default) and/or a [Google AI Studio](https://aistudio.google.com/apikey) `GEMINI_API_KEY` (backup)
- Optional: `ffmpeg` for video frame thumbnails
