"""Updater: version logic, trust checks, checksum, safe unzip, helper script. No network, no GUI."""
import hashlib
import io
import os
import sys
import zipfile

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import updater as up  # noqa: E402
import version  # noqa: E402

REPO = "club/rov-control"
BASE = f"https://github.com/{REPO}/releases/download/v1.2.0/"


def release(tag="v1.2.0", assets=None, **kw):
    if assets is None:
        assets = {up.ASSET: BASE + up.ASSET, up.ASSET + ".sha256": BASE + up.ASSET + ".sha256"}
    return {"tag_name": tag, "body": "notes", "html_url": "https://github.com/x",
            "assets": [{"name": n, "browser_download_url": u} for n, u in assets.items()], **kw}


def test_version_parsing_and_comparison():
    assert up.parse_version("v1.2.3") == (1, 2, 3)
    assert up.is_newer("v1.10.0", "1.9.9") and not up.is_newer("1.0.0", "1.0.0")
    assert up.is_newer("1.0.1", "1.0") and not up.is_newer("1.0", "1.0.0")
    with pytest.raises(up.UpdateError):
        up.parse_version("latest")
    assert up.parse_version(version.__version__)


def test_check_finds_newer_release():
    u = up.check(REPO, "1.0.0", fetch=lambda url: release())
    assert u.version == "1.2.0" and u.url.endswith(up.ASSET) and u.sha_url.endswith(".sha256")


def test_check_up_to_date_draft_and_prerelease_are_ignored():
    assert up.check(REPO, "1.2.0", fetch=lambda url: release()) is None
    assert up.check(REPO, "1.0.0", fetch=lambda url: release(prerelease=True)) is None
    assert up.check(REPO, "1.0.0", fetch=lambda url: release(draft=True)) is None


def test_check_refuses_untrusted_or_incomplete_releases():
    no_sha = {up.ASSET: BASE + up.ASSET}
    with pytest.raises(up.UpdateError, match="checksum"):
        up.check(REPO, "1.0.0", fetch=lambda url: release(assets=no_sha))
    evil = {up.ASSET: "https://evil.example/x.zip", up.ASSET + ".sha256": BASE + up.ASSET + ".sha256"}
    with pytest.raises(up.UpdateError, match="unexpected download address"):
        up.check(REPO, "1.0.0", fetch=lambda url: release(assets=evil))
    with pytest.raises(up.UpdateError):
        up.check(REPO, "1.0.0", fetch=lambda url: release(assets={}))
    with pytest.raises(up.UpdateError, match="no releases"):
        up.check(REPO, "1.0.0", fetch=lambda url: {})


def test_verify_checksum(tmp_path):
    f = tmp_path / "a.zip"
    f.write_bytes(b"hello")
    good = hashlib.sha256(b"hello").hexdigest()
    up.verify(str(f), f"{good}  ROV-Control-windows.zip\n")          # ok, file kept
    assert f.exists()
    with pytest.raises(up.UpdateError, match="does not match"):
        up.verify(str(f), "0" * 64)
    assert not f.exists()                                           # tampered download is deleted
    with pytest.raises(up.UpdateError, match="not a SHA-256"):
        up.verify(str(f), "garbage")


def make_zip(path, names):
    with zipfile.ZipFile(path, "w") as z:
        for n in names:
            z.writestr(n, "x")


def test_stage_extracts_and_finds_exe(tmp_path):
    z = tmp_path / "u.zip"
    make_zip(z, ["ROV Control/ROV Control.exe", "ROV Control/_internal/a.dll", "ROV Control/config/system.yaml"])
    staged = up.stage(str(z), str(tmp_path / "new"))
    assert os.path.isfile(os.path.join(staged, up.EXE))


def test_stage_blocks_zip_slip_and_wrong_zip(tmp_path):
    z = tmp_path / "evil.zip"
    make_zip(z, ["ROV Control/ROV Control.exe", "../../evil.txt"])
    with pytest.raises(up.UpdateError, match="unsafe path"):
        up.stage(str(z), str(tmp_path / "new"))
    assert not (tmp_path / "evil.txt").exists()
    z2 = tmp_path / "wrong.zip"
    make_zip(z2, ["something/else.txt"])
    with pytest.raises(up.UpdateError, match="does not contain"):
        up.stage(str(z2), str(tmp_path / "new2"))


def test_download_reports_progress_and_wraps_errors(tmp_path):
    class R(io.BytesIO):
        headers = {"Content-Length": "200000"}
        def __enter__(self): return self
        def __exit__(self, *a): return False
    seen = []
    up.download("u", str(tmp_path / "f"), lambda d, t: seen.append((d, t)), opener=lambda u: R(b"a" * 200000))
    assert seen[-1] == (200000, 200000) and (tmp_path / "f").stat().st_size == 200000

    def boom(u):
        raise OSError("offline")
    with pytest.raises(up.UpdateError, match="download failed"):
        up.download("u", str(tmp_path / "g"), opener=boom)


def test_download_and_stage_end_to_end(tmp_path):
    z = tmp_path / "src.zip"
    make_zip(z, ["ROV Control/ROV Control.exe"])
    data = z.read_bytes()
    sha = hashlib.sha256(data).hexdigest().encode()

    class R(io.BytesIO):
        def __init__(self, b): super().__init__(b); self.headers = {}
        def __enter__(self): return self
        def __exit__(self, *a): return False

    u = up.Update("1.2.0", "https://x/ROV-Control-windows.zip", "https://x/ROV-Control-windows.zip.sha256")
    staged = up.download_and_stage(u, workdir=str(tmp_path / "w"), opener=lambda url: R(data if url.endswith(".zip") else sha))
    assert os.path.isfile(os.path.join(staged, up.EXE))

    bad = hashlib.sha256(b"something else").hexdigest().encode()          # tampered download
    with pytest.raises(up.UpdateError, match="does not match"):
        up.download_and_stage(u, workdir=str(tmp_path / "w2"), opener=lambda url: R(data if url.endswith(".zip") else bad))
    assert not (tmp_path / "w2" / up.ASSET).exists()


def test_helper_script_keeps_user_settings_and_waits_for_exit():
    s = up.helper_script(r"C:\t\new\ROV Control", r"C:\Apps\ROV Control", 4321)
    assert r'"C:\Apps\ROV Control\config"' in s and '"rov-control.log"' in s   # excluded from the copy (full path)
    assert '/XD "config"' not in s
    assert "/XD" in s and "/XF" in s
    assert "4321" in s and "goto wait" in s                         # waits for the running app
    assert s.index("robocopy") < s.index("start ")                  # copy first, then restart
    assert s.count("\r\n") >= 6


def test_apply_windows_refuses_when_not_writable(tmp_path, monkeypatch):
    if os.name != "nt":
        with pytest.raises(up.UpdateError, match="Windows"):
            up.apply_windows("s", str(tmp_path))
        return
    monkeypatch.setattr(up, "app_dir_writable", lambda d: False)
    with pytest.raises(up.UpdateError, match="not writable"):
        up.apply_windows("s", str(tmp_path))
