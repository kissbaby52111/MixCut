"""Download pinned upstream source snapshots and portable media tools."""
from __future__ import annotations
import argparse
import concurrent.futures
import hashlib
import json
import pathlib
import shutil
import subprocess
import sys
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
PINS = {
    "socai": ("socai-io/socai", "778bdd1669a460c9beff80cd9faf8efb1bc5ef23"),
    "VideoMatrix": ("w2968066/VideoMatrix--", "63741a5acdb7e6cb0160ffb61d853e98dbf937a7"),
    "FireRed-OpenStoryline": ("FireRedTeam/FireRed-OpenStoryline", "c9e945215586f45c12a61c1951ee9a8e9c43a027"),
    "pyJianYingDraft": ("GuanYixuan/pyJianYingDraft", "1e228479dda361e312e85f6037002346cf6dca9a"),
}

def download(url: str, path: pathlib.Path, *, timeout: int = 900) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > 0:
        return path
    partial = path.with_suffix(path.suffix + ".partial")
    cmd = ["curl.exe", "-sfL", "--show-error", "--retry", "2", "--connect-timeout", "15", "--max-time", str(timeout), "-o", str(partial), url]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)
    partial.replace(path)
    return path

def unpack_source(archive: pathlib.Path, dest: pathlib.Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        for item in zf.infolist():
            parts = pathlib.PurePosixPath(item.filename).parts[1:]
            if not parts or item.is_dir():
                continue
            target = dest.joinpath(*parts).resolve()
            if not target.is_relative_to(dest.resolve()):
                raise RuntimeError("Unsafe archive path")
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(item) as src, target.open("wb") as out:
                shutil.copyfileobj(src, out)

def source(name: str) -> dict:
    repo, sha = PINS[name]
    archive = download(f"https://codeload.github.com/{repo}/zip/{sha}", ROOT / ".runtime/downloads" / f"{name}-{sha[:8]}.zip")
    dest = ROOT / "vendor" / name
    unpack_source(archive, dest)
    print(f"Source ready: {name} @ {sha[:12]}", flush=True)
    return {"name": name, "repository": f"https://github.com/{repo}", "commit": sha, "archive_sha256": hashlib.file_digest(archive.open("rb"), "sha256").hexdigest(), "path": str(dest.relative_to(ROOT))}

def apply_vendor_patch() -> None:
    path = ROOT / "vendor/pyJianYingDraft/pyJianYingDraft/__init__.py"
    text = path.read_text(encoding="utf-8")
    if "ENABLE_WINDOWS_AUTOMATION" in text:
        return
    if text.count("if ISWIN:") != 2:
        raise RuntimeError("Unexpected pyJianYingDraft version; no automatic patch applied")
    text = text.replace("import sys\n", "import sys\nimport os\n", 1)
    text = text.replace('ISWIN = sys.platform == "win32"', 'ISWIN = sys.platform == "win32"\n# MixCut patch: pure draft mode does not import Windows UI Automation.\nENABLE_WINDOWS_AUTOMATION = ISWIN and os.environ.get("PYJIANYINGDRAFT_NO_UI") != "1"')
    text = text.replace("if ISWIN:", "if ENABLE_WINDOWS_AUTOMATION:")
    path.write_text(text, encoding="utf-8")

def media_tools() -> dict:
    dest = ROOT / ".runtime/ffmpeg"
    if (dest / "ffmpeg.exe").exists() and (dest / "ffprobe.exe").exists():
        return {"ffmpeg": str(dest / "ffmpeg.exe"), "ffprobe": str(dest / "ffprobe.exe")}
    archive = download("https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-9.0.2-essentials_build.zip", ROOT / ".runtime/downloads/ffmpeg-9.0.2.zip", timeout=600)
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        for item in zf.infolist():
            base = pathlib.PurePosixPath(item.filename).name
            if base in {"ffmpeg.exe", "ffprobe.exe", "LICENSE", "LICENSE.txt"}:
                with zf.open(item) as src, (dest / base).open("wb") as out:
                    shutil.copyfileobj(src, out)
    return {"ffmpeg": str(dest / "ffmpeg.exe"), "ffprobe": str(dest / "ffprobe.exe")}

def socai_binary() -> dict:
    dest = ROOT / ".runtime/socai"
    exe = dest / "socai.exe"
    if exe.exists():
        return {"socai": str(exe), "version": "v0.6.9"}
    url = "https://github.com/socai-io/socai/releases/download/v0.6.9/socai-cli-windows-x86_64.zip"
    archive = download(url, ROOT / ".runtime/downloads/socai-cli-v0.6.9.zip", timeout=180)
    sums = download(url + ".sha256", archive.with_suffix(".zip.sha256"), timeout=60)
    expected = sums.read_text().split()[0].lower()
    actual = hashlib.file_digest(archive.open("rb"), "sha256").hexdigest()
    if expected != actual:
        raise RuntimeError("socai SHA256 mismatch")
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        matches = [x for x in zf.infolist() if pathlib.PurePosixPath(x.filename).name.lower() == "socai.exe"]
        if len(matches) != 1:
            raise RuntimeError("socai.exe missing from official release")
        with zf.open(matches[0]) as src, exe.open("wb") as out:
            shutil.copyfileobj(src, out)
    return {"socai": str(exe), "version": "v0.6.9", "sha256": actual}

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sources-only", action="store_true")
    parser.add_argument("--runtime-only", action="store_true")
    parser.add_argument("--all-sources", action="store_true", help="Also fetch optional upstream reference projects")
    args = parser.parse_args()
    runtime_file = ROOT / ".runtime/runtime.json"
    runtime = json.loads(runtime_file.read_text(encoding="utf-8")) if runtime_file.exists() else {}
    runtime["python"] = sys.executable
    if not args.runtime_only:
        results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            for result in pool.map(source, PINS if args.all_sources else ["pyJianYingDraft"]):
                results.append(result)
        apply_vendor_patch()
        manifest = ROOT / "VENDOR_MANIFEST.json"
        previous = json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else []
        updated = {entry["name"]: entry for entry in previous}
        updated.update({entry["name"]: entry for entry in results})
        manifest.write_text(json.dumps(list(updated.values()), ensure_ascii=False, indent=2), encoding="utf-8")
    runtime_file.parent.mkdir(exist_ok=True)
    runtime_file.write_text(json.dumps(runtime, ensure_ascii=False, indent=2), encoding="utf-8")
    if not args.sources_only:
        from fetch_runtime import main as fetch_main
        # bootstrap arguments belong to this parser, not fetch_runtime's parser.
        sys.argv = [sys.argv[0]]
        fetch_main()

if __name__ == "__main__":
    main()
