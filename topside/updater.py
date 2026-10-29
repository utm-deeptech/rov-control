"""Software updates for the packaged Windows app (no Qt in here, so it is unit-tested without a GUI or network).

Flow: check() asks GitHub Releases for the latest release -> download() the zip and its .sha256 -> verify() ->
stage() extracts it safely -> apply_windows() starts a tiny helper that waits for the app to exit, copies the new
files over the old ones (keeping config/ and the log) and restarts the app.

Safety: only HTTPS downloads from the configured repo's releases, a release without a checksum file is refused, a
file that does not match its checksum is deleted, zip entries cannot escape the staging folder, and the app never
updates while the ROV is armed (enforced by the caller). Source installs update with `rov update` / `make update`.
"""
import hashlib
import json
import ntpath
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile

ASSET = "ROV-Control-windows.zip"
EXE = "ROV Control.exe"
KEEP_DIRS = ("config",)                 # user settings: Wi-Fi IP, thruster wiring, RC calibration
KEEP_FILES = ("rov-control.log",)


class UpdateError(Exception):
    pass


class Update:
    def __init__(self, version, url, sha_url, notes="", page=""):
        self.version, self.url, self.sha_url, self.notes, self.page = version, url, sha_url, notes, page


def parse_version(text):
    m = re.match(r"\s*v?(\d+(?:\.\d+)*)", str(text))
    if not m:
        raise UpdateError(f"cannot read version '{text}'")
    return tuple(int(x) for x in m.group(1).split("."))


def is_newer(latest, current):
    a, b = list(parse_version(latest)), list(parse_version(current))
    n = max(len(a), len(b))
    return a + [0] * (n - len(a)) > b + [0] * (n - len(b))


def fetch_json(url, timeout=6):
    req = urllib.request.Request(url, headers={"User-Agent": "rov-control-updater",
                                               "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise UpdateError("no release published yet (or the repository is private / the name in update.repo is wrong)") from e
        if e.code == 403:
            raise UpdateError("GitHub is rate-limiting this network; try again later") from e
        raise UpdateError(f"GitHub answered HTTP {e.code}") from e
    except Exception as e:
        raise UpdateError(f"could not reach GitHub: {e}") from e


def check(repo, current, asset=ASSET, fetch=fetch_json):
    """The newer release as an Update, or None if up to date. Raises UpdateError if it can't tell / can't be trusted."""
    data = fetch(f"https://api.github.com/repos/{repo}/releases/latest")
    tag = data.get("tag_name")
    if not tag:
        raise UpdateError("no releases published yet")
    if data.get("draft") or data.get("prerelease"):
        return None
    if not is_newer(tag, current):
        return None
    urls = {a.get("name"): a.get("browser_download_url", "") for a in data.get("assets", [])}
    base = f"https://github.com/{repo}/releases/download/"
    if asset not in urls:
        raise UpdateError(f"release {tag} has no {asset}")
    if asset + ".sha256" not in urls:
        raise UpdateError(f"release {tag} has no checksum ({asset}.sha256): refusing to update")
    for u in (urls[asset], urls[asset + ".sha256"]):
        if not u.startswith(base):
            raise UpdateError(f"unexpected download address: {u}")
    return Update(tag.lstrip("v"), urls[asset], urls[asset + ".sha256"], data.get("body") or "", data.get("html_url", ""))


def _open(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "rov-control-updater"})
    return urllib.request.urlopen(req, timeout=timeout)


def download(url, dest, progress=None, opener=_open):
    """Stream url to dest. progress(done_bytes, total_bytes) is called as it goes."""
    try:
        with opener(url) as r, open(dest, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            while True:
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
    except OSError as e:
        raise UpdateError(f"download failed: {e}") from e


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify(path, checksum_text):
    """checksum_text is the .sha256 file ('<hex>  name' or just '<hex>'). Deletes the file if it does not match."""
    parts = checksum_text.split()
    want = parts[0].lower() if parts else ""
    if not re.fullmatch(r"[0-9a-f]{64}", want):
        raise UpdateError("checksum file is not a SHA-256")
    if sha256_of(path) != want:
        os.remove(path)
        raise UpdateError("downloaded file does not match its checksum: deleted, NOT installed")


def stage(zip_path, staging_dir):
    """Extract the release zip into staging_dir (zip-slip safe); returns the folder that contains the app exe."""
    root = os.path.realpath(staging_dir)
    os.makedirs(root, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        for name in z.namelist():
            target = os.path.realpath(os.path.join(root, name))
            if target != root and not target.startswith(root + os.sep):
                raise UpdateError(f"unsafe path in update zip: {name}")
        z.extractall(root)
    for here, _, files in os.walk(root):
        if EXE in files:
            return here
    raise UpdateError(f"update zip does not contain {EXE}")


def helper_script(staged, app_dir, pid, exe=EXE):
    """cmd script: wait for the app (pid) to exit, copy new files over, restart. config/ and the log are kept.
    Always Windows paths (ntpath), whatever OS builds the text."""
    # full paths: a bare name would also skip any library folder called "config" deeper in the tree
    xd = " ".join('"' + ntpath.join(staged, d) + '" "' + ntpath.join(app_dir, d) + '"' for d in KEEP_DIRS)
    xf = " ".join(f'"{f}"' for f in KEEP_FILES)
    sysdir = "%SystemRoot%\\System32\\"     # full paths: Git's GNU find/ping earlier in PATH would break the waiting
    return "\r\n".join([
        "@echo off",
        ":wait",
        f'{sysdir}tasklist.exe /FI "PID eq {pid}" /NH 2>nul | {sysdir}find.exe " {pid} " >nul && '
        f'({sysdir}ping.exe -n 2 127.0.0.1 >nul & goto wait)',
        f'{sysdir}robocopy.exe "{staged}" "{app_dir}" /E /IS /IT /XD {xd} /XF {xf} /R:10 /W:1 >nul',
        f'if errorlevel 8 (echo Update failed: files were in use or not writable > "{app_dir}\\update-error.txt") '
        f'else (del "{app_dir}\\update-error.txt" 2>nul)',
        f'start "" "{ntpath.join(app_dir, exe)}"',
        f'rd /s /q "{ntpath.dirname(staged)}" 2>nul',
        "",
    ])


def app_dir_writable(app_dir):
    return os.access(app_dir, os.W_OK)


def apply_windows(staged, app_dir, pid=None):
    """Start the helper; the caller must exit the app right after. Windows packaged app only."""
    if os.name != "nt":
        raise UpdateError("in-app update is for the packaged Windows app; use `make update` on Linux")
    if not app_dir_writable(app_dir):
        raise UpdateError(f"{app_dir} is not writable: move the app to a normal folder (e.g. Documents) and try again")
    pid = pid or os.getpid()
    script = os.path.join(tempfile.mkdtemp(prefix="rov_upd_"), "update.cmd")
    with open(script, "w", newline="") as f:
        f.write(helper_script(staged, app_dir, pid))
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(["cmd", "/c", script], creationflags=flags, close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def is_packaged():
    return bool(getattr(sys, "frozen", False))


def app_dir():
    return os.path.dirname(sys.executable)


def download_and_stage(update, progress=None, workdir=None, opener=_open):
    """Download + verify + extract. Returns the staged folder. Raises UpdateError (nothing is installed on failure)."""
    work = workdir or tempfile.mkdtemp(prefix="rov_update_")
    os.makedirs(work, exist_ok=True)
    zip_path = os.path.join(work, ASSET)
    download(update.url, zip_path, progress, opener)
    sha_path = os.path.join(work, ASSET + ".sha256")
    download(update.sha_url, sha_path, opener=opener)
    with open(sha_path, encoding="utf-8") as f:
        verify(zip_path, f.read())
    return stage(zip_path, os.path.join(work, "new"))


def cleanup(path):
    shutil.rmtree(path, ignore_errors=True)
