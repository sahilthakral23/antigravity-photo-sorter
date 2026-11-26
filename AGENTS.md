# Gemini Photo Sorter — Instructions & Playbook for Agents

Read this file before making any change to this repository. For full API specs, data schemas, and module internals, also read [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

---

## 1. Non-Negotiable Ground Rules

1. **Never move, rename, or delete the user's original photos.** Every script must treat the source folder as strictly read-only and write sorted copies only to the destination folder. Never run destructive commands on user photo directories. If the user wants duplicates deleted, point them to `<dest>/duplicates.csv` and let them decide.
2. **Always estimate and ask before spending Gemini API tokens.** The default labeler is **Antigravity CLI (`agy`)**. Before invoking `scripts/label_gemini_api.py` on behalf of the user, run `--estimate`, report file count + estimated tokens, and wait for explicit confirmation.
3. **Keep user photos and keys private.** Never commit, log, or print secrets from `.env`, and never commit anything inside `dashboard/data/`, `dashboard/peek/`, `dashboard/demo/`, or `work/`.
4. **Preserve zero-build simplicity.** Keep the frontend plain HTML/CSS/JS (no bundler/npm build step) and keep `requirements.txt` minimal (`pillow`, `pillow-heif`, `imagehash`).

---

## 2. Environment & Quick Commands

- **Python venv:**
  - Windows: `.venv\Scripts\python.exe` (installed via Python 3.12; always set `$env:PYTHONUTF8 = '1'` in PowerShell).
  - macOS / Linux: `.venv/bin/python`.
- **Dashboard URL:** `http://127.0.0.1:8765` (override port with `PORT=8766`). Demo mode: `http://127.0.0.1:8765/?demo`.
- **Launchers:**
  - Windows: `setup.bat`, `start.bat`, `start.bat --demo`, or double-click `Open Photo Sorter.bat`.
  - macOS / Linux: `./setup.sh`, `./start.sh`, `./start.sh --demo`, or `Open Photo Sorter.command`.

| Step | Command (Windows; replace `.venv\Scripts\python.exe` with `.venv/bin/python` on macOS/Linux) |
|---|---|
| Start server | `$env:PYTHONUTF8='1'; .venv\Scripts\python.exe scripts\server.py` |
| Scan photos | `.venv\Scripts\python.exe scripts\scan.py "<photos folder>" [--limit 200]` |
| Find duplicates | `.venv\Scripts\python.exe scripts\dupes.py` (visual preview: `work\duplicates.jpg`) |
| Build contact sheets | `.venv\Scripts\python.exe scripts\sheets.py [--sample]` |
| Label (Antigravity CLI) | `.venv\Scripts\python.exe scripts\label_agy.py [--only N] [--model <slug>]` |
| Label (Gemini API backup) | `.venv\Scripts\python.exe scripts\label_gemini_api.py --estimate` then without `--estimate` |
| Copy into folders | `.venv\Scripts\python.exe scripts\apply.py "<output folder>"` |
| Regenerate demo | `.venv\Scripts\python.exe scripts\demo.py` |

---

## 3. Modification Playbooks (How to Update Anything Safely)

### A. Updating or Adding Default Folders (`categories.json`)
- Edit `categories.json`. Every entry requires: `key` (unique slug), `kind` (`"photo"` or `"video"`), `label`, `emoji`, and `rule` (plain-English description read by Gemini).
- **Invariant:** Keep at least one `"photo"` folder and one `"video"` folder, and keep the **last folder of each kind** as a catch-all (`screenshots` for photos, `vclips` for videos) because `apply.py` uses the last entry of each kind as fallback.
- If you add/remove keys in `categories.json`, also update `WEIGHTS` in `scripts/demo.py` and re-run `scripts/demo.py` if demo mode should showcase the new category.
- Note: `server.py` hashes `categories.json` (`categories_fingerprint()`) and automatically invalidates `work/labels/` when categories change before the next sort.

### B. Updating Antigravity CLI (`agy`) Labeling (`scripts/label_agy.py`)
- `label_agy.py` discovers `agy` via `common.find_agy()` and invokes:
  `agy -p <prompt> --output-format json --json-schema <work/label_schema.json> --disable-slash-commands --print-timeout 15m --add-dir <work> [--model <slug>]`
- To add or change model choices shown in the UI, update the `<select>` options for `agySelect` in `dashboard/app.js` (search for `agyModel`). Valid slugs come from `agy models`.
- **Do not change the JSON schema to an open dictionary** (`additionalProperties: true` with empty `properties: {}`); Gemini schema validation rejects that. Keep `{"labels": [{"id": "...", "key": "..."}]}`.
- Batch outputs must be written to `work/labels/<prefix>_NN.json` as flat `{"<id>": "<key>"}` JSON objects so `server.py:live_labels()` and `apply.py` pick them up seamlessly.

### C. Updating Gemini API Backup Labeling (`scripts/label_gemini_api.py`)
- Uses stdlib `urllib.request` directly against `https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent` with `x-goog-api-key` header and `responseSchema`.
- To add/update models, update the default `--model` in `scripts/label_gemini_api.py` and `apiSelect` in `dashboard/app.js`.
- Keep `--estimate` working with zero network calls and `TOKENS_PER_SHEET` synced with `dashboard/app.js`.

### D. Adding a New Pipeline Phase or Server API Endpoint (`scripts/server.py`)
- Add `GET` routes in `H.do_GET()` and `POST` routes in `H.do_POST()` (which automatically enforces the localhost `Origin` check and JSON error wrapping).
- Any new pipeline script run via `run_phase()` **must** print `PROGRESS <done> <total>` with `flush=True` so the progress bar and ETA update smoothly.
- If adding a phase to `sort_all()`, also add its display label in `NAMES` and phase pill name in `runCard()`, plus an estimate entry in `phaseEstimates()` inside `dashboard/app.js`.
- Remember: **always restart `scripts/server.py` after editing Python backend files** so the running server picks up changes.

### E. Updating the Dashboard UI (`dashboard/`)
- Theme colors live in `dashboard/tokens.css` (both dark default and `:root[data-theme="light"]`).
- Shared primitives (buttons, modals, KPIs, segmented controls) live in `dashboard/components.css`.
- Page layout, 3D stage, folder picker modal, and lightbox styles live in `dashboard/app.css`.
- All logic is in `dashboard/app.js` using the lightweight `el(tag, attrs, ...kids)` helper and inlined SVG `ICONS` dictionary.
- Whenever you inject new `[data-icon]` elements dynamically into a container, call `paintIcons(container)` so their SVGs render.
- Always test both **normal mode** (`/`) and **demo mode** (`/?demo`), plus both **light and dark themes**.

### F. Updating Duplicate Detection or File Copying (`scripts/dupes.py`, `scripts/apply.py`)
- `dupes.py`: near-duplicate threshold is `phash` distance `<= 4`; PNG pixel-diff threshold is `> 0.4%` of pixels differing by `> 40`. Changing these only affects `dupes.json` and `work/duplicates.jpg`.
- `apply.py`: never change `copy()` to move/unlink originals. If adding an optional hard-link mode in the future, make it **opt-in** and warn users that editing a hard-linked copy mutates the original. Keep `safe_folder_name()` sanitizing Windows-illegal characters (`/ \ : * ? " < > |`).

---

## 4. Known Gotchas & Platform Pitfalls (Do Not Repeat These!)

1. **Windows Agent Desktop Isolation (`Desktop=exebox-...`):**
   - When an AI agent starts `scripts/server.py` via its shell tool on Windows, Windows runs it on an isolated non-visible desktop (`exebox-...`), NOT the user's `Default` desktop. Any GUI popup (`tkinter`, WinForms, `explorer.exe`) launched from that server will be invisible to the user.
   - **Solution already in place:** `dashboard/app.js` uses the in-browser Folder Browser modal (`GET /api/browse` + `POST /api/mkdir`) as the primary picker, and `server.py:is_interactive_desktop()` only enables the optional **OS dialog** button when running on `Desktop=Default` (e.g., launched via `Open Photo Sorter.bat`). Never replace the in-browser picker with an OS-only dialog.
2. **Windows PowerShell 5.1 Differences:**
   - PowerShell 5.1 does **not** support `&&` (use `$ErrorActionPreference = 'Stop'; cmd1; cmd2`).
   - `.NET Framework 4.8`'s `FolderBrowserDialog` does **not** have `.UseDescriptionForTitle` (that property only exists in .NET 5+ / PowerShell 7).
3. **Windows `cp1252` Encoding Errors:**
   - `categories.json` contains emojis and filenames can contain Unicode. Always open files with `encoding="utf-8"` and run Python subprocesses with `PYTHONUTF8=1` and `PYTHONIOENCODING=utf-8`.
4. **Windows Drive Roots in Path Normalization:**
   - Never blindly call `.rstrip("/\\")` on Windows paths without guarding `C:\` → `C:`. Always use `common.clean_path()`.
5. **`tkinter` Withdrawn Parent Bug on Windows:**
   - In `pick_folder()`, do **not** combine `root.withdraw()` with `filedialog.askdirectory(parent=root)` — on Windows it hides the dialog from the taskbar and behind the browser. Keep the `1x1` zero-alpha topmost root pattern (`root.attributes('-alpha', 0.0)` + `root.attributes('-topmost', True)`).
6. **Stale Server Processes on Port 8765:**
   - Before restarting `server.py`, ensure any existing listener on `127.0.0.1:8765` (and any lingering `server.py` child process) is stopped so you don't test against an old process.

---

## 5. Verification Checklist After Making Changes

Always run these checks after edits before finishing:

1. **Syntax check all Python & JS files:**
   ```powershell
   .venv\Scripts\python.exe -m py_compile scripts\common.py scripts\scan.py scripts\dupes.py scripts\sheets.py scripts\label_agy.py scripts\label_gemini_api.py scripts\apply.py scripts\demo.py scripts\server.py
   node --check dashboard\app.js
   ```
2. **End-to-end pipeline smoke test (in an isolated scratch folder — never on user photos):**
   - Copy 4–5 images from `dashboard/demo/thumbs/` (include 1 exact duplicate and 1 `.png`) into a temporary scratch folder.
   - Run `scan.py` → `dupes.py` → `sheets.py` → `label_gemini_api.py --estimate` → `label_agy.py --only 1 --model gemini-3.8-flash-low` → `apply.py "<scratch_out>"`.
   - Confirm exact duplicate goes to `_Review/duplicates/`, `sorted.csv` and `duplicates.csv` exist, and all files are labeled.
   - **Clean up `dashboard/data/` and `work/`** afterwards so the user's dashboard starts clean (keep `dashboard/demo/`).
3. **Live HTTP verification:**
   - Restart `scripts/server.py` and verify:
     - `GET /api/state` returns HTTP `200` with `agy_cli: true`, `can_pick: true`, and expected `categories` count.
     - `GET /api/browse` returns valid `path`, `dirs`, and `places`.
     - `GET /` and `GET /demo/inventory.json` return HTTP `200`.
