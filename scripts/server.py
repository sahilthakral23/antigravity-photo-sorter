"""The dashboard app.  python scripts/server.py   ->  http://127.0.0.1:8765

Serves dashboard/ plus a small local API so the page can do everything itself:
pick folders (native dialog on Windows, macOS & Linux), peek at a folder, run the
whole sort (scan, duplicates, Antigravity CLI or Gemini API labels, build folders)
with live progress, edit folders, browse photos at full size, move a photo to
another folder, and open folders in Explorer / Finder.
Listens on 127.0.0.1 only: nothing is reachable from other computers.
"""
import glob
import hashlib
import io
import json
import mimetypes
import os
import random
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(__file__))
from common import (
    CATEGORIES,
    DATA,
    IMG,
    LABELS,
    ROOT,
    VID,
    WORK,
    clean_path,
    find_agy,
    get_gemini_key,
    load,
    save,
    save_gemini_key,
)

PORT = int(os.environ.get("PORT", 8765))
DASH = os.path.join(ROOT, "dashboard")
PEEK = os.path.join(DASH, "peek")
OVERRIDES = os.path.join(WORK, "overrides.json")
SCRIPTS = os.path.join(ROOT, "scripts")
PY = sys.executable

job = {
    "step": None,
    "running": False,
    "log": [],
    "code": None,
    "phase": None,
    "phases": [],
    "done": 0,
    "total": 0,
    "started": None,
    "phase_started": None,
    "timings": {},
    "labeler": "agy",
}


def is_interactive_desktop():
    """Return True if a GUI dialog spawned by this process will be visible to the user."""
    if sys.platform.startswith("win"):
        try:
            import ctypes
            u32 = ctypes.windll.user32
            k32 = ctypes.windll.kernel32
            hdesk = u32.GetThreadDesktop(k32.GetCurrentThreadId())
            buf = ctypes.create_unicode_buffer(256)
            needed = ctypes.c_ulong(0)
            if u32.GetUserObjectInformationW(hdesk, 2, buf, ctypes.sizeof(buf), ctypes.byref(needed)):
                return buf.value.lower() == "default"
        except Exception:
            return False
        return False
    if sys.platform == "darwin":
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")) and bool(
        shutil.which("zenity") or shutil.which("kdialog")
    )


def quick_places():
    """Common starting folders and drives for the in-browser folder picker."""
    home = os.path.expanduser("~")
    places = []
    seen = set()

    def add(label, path, icon="folder"):
        if not path:
            return
        norm = os.path.normpath(path)
        key = norm.lower()
        if key not in seen and os.path.isdir(norm):
            seen.add(key)
            places.append({"label": label, "path": norm, "icon": icon})

    for name in ("Pictures", "Desktop", "Downloads", "Documents"):
        add(name, os.path.join(home, name))
    for env_var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        od = os.environ.get(env_var)
        if od:
            add("OneDrive Pictures", os.path.join(od, "Pictures"))
            add("OneDrive", od)
    add("Home", home)

    if sys.platform.startswith("win"):
        import string
        for letter in string.ascii_uppercase:
            drive = f"{letter}:\\"
            if os.path.isdir(drive):
                add(f"Drive ({letter}:)", drive, icon="drive")
    else:
        add("Root (/)", "/", icon="drive")
    return places


def browse_dir(path=""):
    """List subdirectories and quick media counts for the in-browser folder picker."""
    target = clean_path(path) if path else ""
    if not target or not os.path.isdir(target):
        home = os.path.expanduser("~")
        pics = os.path.join(home, "Pictures")
        target = pics if os.path.isdir(pics) else home

    target = os.path.abspath(target)
    parent = os.path.dirname(target)
    if parent == target:
        parent = None

    dirs = []
    photos, videos = 0, 0
    try:
        with os.scandir(target) as it:
            for entry in it:
                name = entry.name
                if name.startswith(".") or name.startswith("$"):
                    continue
                try:
                    if entry.is_dir(follow_symlinks=False):
                        dirs.append({"name": name, "path": entry.path})
                    elif entry.is_file(follow_symlinks=False):
                        ext = os.path.splitext(name)[1].lower()
                        if ext in IMG:
                            photos += 1
                        elif ext in VID:
                            videos += 1
                except OSError:
                    continue
    except PermissionError:
        raise ValueError("Permission denied for that folder.")
    dirs.sort(key=lambda d: d["name"].lower())
    return {
        "path": target,
        "parent": parent,
        "dirs": dirs,
        "photos": photos,
        "videos": videos,
        "places": quick_places(),
    }


def state():
    inv = load(os.path.join(DATA, "inventory.json"), [])
    src = load(os.path.join(DATA, "source.json"), {})
    label_files = glob.glob(os.path.join(LABELS, "*.json"))
    agy_path = find_agy()
    return {
        "categories": load(CATEGORIES, []),
        "source": src.get("source"),
        "limit": src.get("limit"),
        "sorted": src.get("sorted"),
        "files": len(inv),
        "bytes": sum(r["bytes"] for r in inv),
        "steps": {
            "scan": bool(inv),
            "dupes": os.path.exists(os.path.join(DATA, "dupes.json")),
            "label": bool(label_files),
            "apply": bool(src.get("sorted")) and os.path.exists(os.path.join(DATA, "labels.json")),
        },
        "agy_cli": bool(agy_path),
        "gemini_key": bool(get_gemini_key()),
        "overrides": len(load(OVERRIDES, {})),
        "job": {
            k: job[k]
            for k in (
                "step",
                "running",
                "code",
                "phase",
                "phases",
                "done",
                "total",
                "started",
                "phase_started",
                "timings",
                "labeler",
            )
        }
        | {"log": job["log"][-40:], "now": time.time()},
        "platform": sys.platform,
        "can_pick": True,
        "native_pick": is_interactive_desktop(),
    }


def live_labels():
    """Labels written so far (Antigravity CLI or Gemini API batches), for the live animation."""
    out = {}
    for f in sorted(glob.glob(os.path.join(LABELS, "*.json"))):
        out.update({str(k): v for k, v in load(f, {}).items()})
    return out


def clear_library():
    """A new folder (or sample size) means the old labels no longer match."""
    for f in (os.path.join(DATA, "labels.json"), os.path.join(DATA, "dupes.json"), OVERRIDES):
        if os.path.exists(f):
            os.remove(f)
    for d in (LABELS, os.path.join(WORK, "sheets")):
        shutil.rmtree(d, ignore_errors=True)


def categories_fingerprint():
    with open(CATEGORIES, "rb") as f:
        return hashlib.sha1(f.read()).hexdigest()[:12]


def stream(cmd):
    """Run one script, keep its output for the log, and read PROGRESS lines."""
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    p = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=ROOT,
        env=env,
    )
    for line in p.stdout:
        line = line.rstrip()
        m = re.match(r"PROGRESS (\d+) (\d+)", line)
        if m:
            job["done"], job["total"] = int(m.group(1)), int(m.group(2))
        else:
            job["log"].append(line)
    return p.wait()


def start_job(step, phases, body, labeler="agy"):
    if job["running"]:
        raise RuntimeError("Something is already running. Wait for it to finish.")
    now = time.time()
    job.update(
        step=step,
        running=True,
        log=[f"$ {step}"],
        code=None,
        phase=None,
        phases=phases,
        done=0,
        total=0,
        started=now,
        phase_started=now,
        timings={},
        labeler=labeler,
    )
    threading.Thread(target=body, daemon=True).start()


def run_phase(name, cmd):
    job.update(phase=name, done=0, total=0, phase_started=time.time())
    code = stream([PY, "-u"] + cmd)
    job["timings"][name] = round(time.time() - job["phase_started"], 1)
    return code


def sort_all(args):
    source = clean_path(args.get("source") or "")
    dest = clean_path(args.get("dest") or "")
    limit = int(args.get("limit") or 0)
    labeler = args.get("labeler") or "agy"
    model = (args.get("model") or "").strip()
    if not source or not os.path.isdir(source):
        raise ValueError("Choose the folder with your photos first.")
    if labeler == "agy" and not find_agy():
        raise ValueError(
            "Antigravity CLI (`agy`) was not found. Install it, or switch to Gemini API under Advanced."
        )
    if labeler == "gemini_api" and not get_gemini_key():
        raise ValueError("Add your Gemini API key under Advanced (or in .env) to use Gemini API.")

    src = load(os.path.join(DATA, "source.json"), {})
    have = os.path.exists(os.path.join(DATA, "inventory.json"))
    need_scan = not have or src.get("source") != source or int(src.get("limit") or 0) != limit
    phases = (["scan"] if need_scan else []) + ["dupes", "label", "apply"]

    def body():
        def fail(code):
            job.update(running=False, code=code)

        if need_scan:
            clear_library()
            if run_phase("scan", [os.path.join(SCRIPTS, "scan.py"), source] + (["--limit", str(limit)] if limit else [])):
                return fail(1)
        if run_phase("dupes", [os.path.join(SCRIPTS, "dupes.py")]):
            return fail(1)
        marker = os.path.join(LABELS, ".categories")
        if load(marker) != categories_fingerprint():
            shutil.rmtree(LABELS, ignore_errors=True)
        os.makedirs(LABELS, exist_ok=True)
        save(marker, categories_fingerprint())

        script = "label_gemini_api.py" if labeler == "gemini_api" else "label_agy.py"
        label_cmd = [os.path.join(SCRIPTS, script)]
        if model:
            label_cmd.extend(["--model", model])
        if run_phase("label", label_cmd):
            total = len(load(os.path.join(DATA, "inventory.json"), []))
            if not total or len(live_labels()) < 0.95 * total:
                return fail(1)
            job["log"].append("A few files could not be labelled; they go to the catch-all folders.")
        if run_phase("apply", [os.path.join(SCRIPTS, "apply.py"), dest]):
            return fail(1)
        job.update(running=False, code=0, phase="done")

    start_job("sort", phases, body, labeler=labeler)


def run_single(step, args):
    cmds = {
        "scan": ["scan.py", args.get("source", "")] + (["--limit", str(args["limit"])] if args.get("limit") else []),
        "dupes": ["dupes.py"],
        "estimate": ["label_gemini_api.py", "--estimate"],
        "gemini_api": ["label_gemini_api.py"],
        "agy": ["label_agy.py"],
        "apply": ["apply.py", args.get("dest", "")],
    }
    if step not in cmds:
        raise ValueError("unknown step")

    def body():
        if step == "scan":
            clear_library()
        code = run_phase(step, [os.path.join(SCRIPTS, cmds[step][0])] + cmds[step][1:])
        job.update(running=False, code=code, phase="done" if code == 0 else step)

    start_job(step, [step], body)


def peek(source):
    """Count what is in a folder and make a few tiny previews, right after it is picked."""
    source = clean_path(source)
    if not source or not os.path.isdir(source):
        raise ValueError("That folder does not exist.")
    photos, videos, size, pics = 0, 0, 0, []
    for dp, dirs, fs in os.walk(source):
        dirs[:] = [d for d in dirs if not d.startswith(".") and not d.endswith(" (sorted)")]
        for f in fs:
            ext = os.path.splitext(f)[1].lower()
            if f.startswith("._"):
                continue
            if ext in IMG:
                photos += 1
                if ext in (".jpg", ".jpeg", ".png", ".heic", ".webp"):
                    pics.append(os.path.join(dp, f))
            elif ext in VID:
                videos += 1
            else:
                continue
            try:
                size += os.path.getsize(os.path.join(dp, f))
            except OSError:
                pass
    shutil.rmtree(PEEK, ignore_errors=True)
    os.makedirs(PEEK, exist_ok=True)
    from PIL import Image, ImageOps
    try:
        from pillow_heif import register_heif_opener
        register_heif_opener()
    except ImportError:
        pass
    out, stamp = [], int(time.time())
    for i, p in enumerate(random.Random(3).sample(pics, min(60, len(pics)))):
        try:
            with Image.open(p) as im:
                im.draft("RGB", (160, 160))
                im = ImageOps.exif_transpose(im).convert("RGB")
                s = min(im.size)
                im = im.crop(((im.width - s) // 2, (im.height - s) // 2, (im.width + s) // 2, (im.height + s) // 2)).resize((64, 64))
                im.save(os.path.join(PEEK, f"{i}.jpg"), quality=70)
                out.append(f"peek/{i}.jpg?{stamp}")
        except Exception:
            pass
    base_dest = source.rstrip("/\\")
    return {
        "source": source,
        "photos": photos,
        "videos": videos,
        "bytes": size,
        "previews": out,
        "suggested_dest": base_dest + " (sorted)",
    }


def open_path(path, reveal=False):
    if sys.platform == "darwin":
        subprocess.run(["open", "-R", path] if reveal else ["open", path])
    elif sys.platform.startswith("win"):
        if reveal:
            subprocess.run(["explorer", f"/select,{os.path.normpath(path)}"])
        else:
            os.startfile(os.path.normpath(path))
    else:
        subprocess.run(["xdg-open", os.path.dirname(path) if reveal else path])


def pick_folder(prompt):
    """Native OS folder picker shown in front of the browser (Windows, macOS, Linux)."""
    safe = (prompt or "Choose a folder").replace('"', "'").replace("`", "")[:120]
    if sys.platform.startswith("win"):
        # Primary: Python's built-in tkinter opens the modern Windows Explorer IFileDialog
        tk_code = (
            "import sys, tkinter as tk\n"
            "from tkinter import filedialog\n"
            "root = tk.Tk()\n"
            "root.geometry('1x1+0+0')\n"
            "root.attributes('-alpha', 0.0)\n"
            "root.attributes('-topmost', True)\n"
            "root.update()\n"
            "path = filedialog.askdirectory(parent=root, title=sys.argv[1], mustexist=False)\n"
            "root.destroy()\n"
            "if path:\n"
            "    print(path)\n"
        )
        kw = {"creationflags": subprocess.CREATE_NO_WINDOW} if hasattr(subprocess, "CREATE_NO_WINDOW") else {}
        r = subprocess.run(
            [PY, "-c", tk_code, safe],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            **kw,
        )
        if r.returncode == 0:
            picked = r.stdout.strip()
            return clean_path(os.path.normpath(picked)) if picked else None

        # Fallback: Windows PowerShell FolderBrowserDialog (.NET Framework 4.8 compatible)
        ps = (
            "Add-Type -AssemblyName System.Windows.Forms; "
            "$f = New-Object System.Windows.Forms.Form; "
            "$f.TopMost = $true; $f.StartPosition = 'CenterScreen'; $f.ShowInTaskbar = $false; "
            "$d = New-Object System.Windows.Forms.FolderBrowserDialog; "
            f"$d.Description = '{safe}'; "
            "$d.ShowNewFolderButton = $true; "
            "if ($d.ShowDialog($f) -eq [System.Windows.Forms.DialogResult]::OK) { Write-Output $d.SelectedPath }; "
            "$f.Dispose()"
        )
        r2 = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-Command", ps],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            **kw,
        )
        return clean_path(r2.stdout.strip()) or None
    if sys.platform == "darwin":
        script = (
            "tell application (path to frontmost application as text)\n"
            f'  POSIX path of (choose folder with prompt "{safe}")\n'
            "end tell"
        )
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        return clean_path(r.stdout.strip()) or None
    if shutil.which("zenity"):
        r = subprocess.run(["zenity", "--file-selection", "--directory", f"--title={safe}"], capture_output=True, text=True)
        return clean_path(r.stdout.strip()) or None
    if shutil.which("kdialog"):
        r = subprocess.run(["kdialog", "--getexistingdirectory", os.path.expanduser("~"), "--title", safe], capture_output=True, text=True)
        return clean_path(r.stdout.strip()) or None
    return None


class H(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=DASH, **k)

    def log_message(self, *a):
        pass

    def send_json(self, obj, code=200):
        b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def end_headers(self):
        if self.path.startswith(("/data/", "/peek/")) or self.path.endswith((".js", ".css", ".html")) or self.path == "/":
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path == "/api/state":
            return self.send_json(state())
        if u.path == "/api/labels":
            return self.send_json(live_labels())
        if u.path == "/api/browse":
            qs = urllib.parse.parse_qs(u.query)
            try:
                return self.send_json(browse_dir((qs.get("path") or [""])[0]))
            except Exception as e:
                return self.send_json({"error": str(e)}, 400)
        m = re.match(r"^/api/file/(\d+)$", u.path)
        if m:
            return self.send_original(int(m.group(1)))
        return super().do_GET()

    def send_original(self, i):
        inv = load(os.path.join(DATA, "inventory.json"), [])
        rec = next((r for r in inv if r["id"] == i), None)
        if not rec or not os.path.exists(rec["path"]):
            return self.send_error(404)
        path = rec["path"]
        if rec["ext"] in (".heic", ".heif", ".tif", ".tiff", ".bmp"):
            from PIL import Image, ImageOps
            try:
                from pillow_heif import register_heif_opener
                register_heif_opener()
            except ImportError:
                pass
            with Image.open(path) as raw:
                im = ImageOps.exif_transpose(raw).convert("RGB")
                im.thumbnail((2400, 2400))
                buf = io.BytesIO()
                im.save(buf, "JPEG", quality=88)
            b = buf.getvalue()
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            return self.wfile.write(b)
        size = os.path.getsize(path)
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if rec["ext"] == ".mov":
            ctype = "video/mp4"
        start, end = 0, size - 1
        rng = self.headers.get("Range")
        if rng:
            m = re.match(r"bytes=(\d*)-(\d*)", rng)
            if m:
                start = int(m.group(1) or 0)
                end = min(int(m.group(2)) if m.group(2) else size - 1, size - 1)
        self.send_response(206 if rng else 200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if rng:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            left = end - start + 1
            try:
                while left > 0:
                    chunk = f.read(min(1 << 20, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def do_POST(self):
        if self.headers.get("Origin") not in (None, f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"):
            return self.send_json({"error": "forbidden"}, 403)
        n = int(self.headers.get("Content-Length", 0) or 0)
        body = json.loads((self.rfile.read(n) or b"{}").decode("utf-8"))
        p = urllib.parse.urlparse(self.path).path
        try:
            if p == "/api/sort":
                sort_all(body)
            elif p == "/api/run":
                run_single(body.get("step"), body)
            elif p == "/api/peek":
                return self.send_json(peek(body.get("source", "")))
            elif p == "/api/key":
                key = (body.get("key") or "").strip()
                save_gemini_key(key)
            elif p == "/api/categories":
                cats = body["categories"]
                keys = [c["key"] for c in cats]
                if len(keys) != len(set(keys)) or not all(c.get("label") and c.get("kind") in ("photo", "video") for c in cats):
                    raise ValueError("Every folder needs a name.")
                if not any(c["kind"] == "photo" for c in cats) or not any(c["kind"] == "video" for c in cats):
                    raise ValueError("Keep at least one photo folder and one video folder.")
                save(CATEGORIES, cats)
                save(os.path.join(DATA, "categories.json"), cats)
            elif p == "/api/move":
                ov = load(OVERRIDES, {})
                ov[str(body["id"])] = body["key"]
                save(OVERRIDES, ov)
                labels = load(os.path.join(DATA, "labels.json"), {})
                labels[str(body["id"])] = body["key"]
                save(os.path.join(DATA, "labels.json"), labels)
            elif p == "/api/open":
                src = load(os.path.join(DATA, "source.json"), {})
                if body.get("id") is not None:
                    rec = next(r for r in load(os.path.join(DATA, "inventory.json"), []) if r["id"] == int(body["id"]))
                    open_path(rec["path"], reveal=True)
                elif src.get("sorted"):
                    folder = body.get("folder")
                    path = os.path.join(src["sorted"], folder) if folder else src["sorted"]
                    open_path(path if os.path.isdir(path) else src["sorted"])
                else:
                    raise ValueError("Sort first, then the folders can be opened.")
            elif p == "/api/pick":
                return self.send_json({"path": pick_folder(body.get("prompt") or "Choose the folder with your photos")})
            elif p == "/api/mkdir":
                parent = clean_path(body.get("parent") or "")
                name = (body.get("name") or "").strip()
                if not parent or not os.path.isdir(parent):
                    raise ValueError("Parent folder does not exist.")
                if not name or any(ch in name for ch in ('/', '\\', ':', '*', '?', '"', '<', '>', '|')) or name in (".", ".."):
                    raise ValueError("Invalid folder name.")
                target = os.path.join(parent, name)
                os.makedirs(target, exist_ok=True)
                return self.send_json({"path": target})
            else:
                return self.send_json({"error": "not found"}, 404)
        except Exception as e:
            return self.send_json({"error": str(e)}, 400)
        return self.send_json(state())


if __name__ == "__main__":
    os.makedirs(DATA, exist_ok=True)
    print(f"Gemini photo sorter running at http://127.0.0.1:{PORT}  (Ctrl+C to stop)", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
