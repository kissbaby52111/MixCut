"""Portable binaries, verified against upstream registry/release digests."""
from __future__ import annotations
import argparse
import base64
import concurrent.futures
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DOWNLOADS = ROOT / ".runtime/downloads"

def fetch(url: str, target: Path, *, size: int | None = None, sha256: str | None = None, sha512: str | None = None) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    def valid():
        if not target.exists():
            return False
        with target.open("rb") as f:
            digest = hashlib.file_digest(f, "sha512" if sha512 else "sha256").digest()
        return base64.b64encode(digest).decode() == sha512 if sha512 else digest.hex() == sha256
    if valid():
        return target
    if size is None:
        response = subprocess.run(["curl.exe", "-sfIL", "--connect-timeout", "12", "--max-time", "30", url], capture_output=True, text=True)
        sizes = re.findall(r"content-length:\s*(\d+)", response.stdout, flags=re.I)
        size = int(sizes[-1]) if sizes else None
    if size:
        chunk_size = math.ceil(size / 8)
        parts_dir = target.parent / (target.name + ".parts")
        parts_dir.mkdir(exist_ok=True)
        def part(index):
            start, end = index*chunk_size, min(size-1, (index+1)*chunk_size-1)
            path = parts_dir / str(index)
            if path.exists() and path.stat().st_size == end-start+1:
                return path
            subprocess.run(["curl.exe", "-sfL", "--show-error", "--retry", "2", "--connect-timeout", "15", "--max-time", "480", "--range", f"{start}-{end}", "-o", str(path), url], check=True)
            if path.stat().st_size != end-start+1:
                raise RuntimeError(f"Server ignored byte range: {target.name}")
            return path
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            parts = list(pool.map(part, range(8)))
        with target.open("wb") as out:
            for path in parts:
                with path.open("rb") as src:
                    shutil.copyfileobj(src, out)
    else:
        subprocess.run(["curl.exe", "-sfL", "--show-error", "--retry", "2", "--connect-timeout", "15", "--max-time", "900", "-o", str(target), url], check=True)
    if not valid():
        raise RuntimeError(f"Integrity verification failed: {target.name}")
    print(f"Verified download: {target.name}", flush=True)
    return target

def ffmpeg() -> dict:
    target = ROOT / ".runtime/ffmpeg/ffmpeg.exe"
    if target.exists():
        return {"ffmpeg": str(target)}
    archive = fetch("https://pypi.tuna.tsinghua.edu.cn/packages/2c/c6/fa760e12a2483469e2bf5058c5faff664acf66cadb4df2ad6205b016a73d/imageio_ffmpeg-0.6.0-py3-none-win_amd64.whl", DOWNLOADS / "imageio_ffmpeg-0.6.0.whl", size=31246824, sha256="02fa47c83703c37df6bfe4896aab339013f62bf02c5ebf2dce6da56af04ffc0a")
    target.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        name = next(x for x in zf.namelist() if x.endswith(".exe"))
        with zf.open(name) as src, target.open("wb") as out:
            shutil.copyfileobj(src, out)
        for name in zf.namelist():
            if "LICENSE" in name.upper() and not name.endswith("/"):
                (target.parent / ("imageio-"+Path(name).name)).write_bytes(zf.read(name))
    return {"ffmpeg": str(target), "ffmpeg_package": "imageio-ffmpeg 0.6.0 / ffmpeg 7.1"}

def ffprobe() -> dict:
    target = ROOT / ".runtime/ffmpeg/ffprobe.exe"
    if target.exists():
        return {"ffprobe": str(target)}
    dist = {"tarball": "https://registry.npmjs.org/@ffprobe-installer/win32-x64/-/win32-x64-5.1.0.tgz", "integrity": "sha512-jMGYeAgkrdn4e2vvYt/qakgHRE3CPju4bn5TmdPfoAm1BlX1mY9cyMd8gf5vSzI8gH8Zq5WQAyAkmekX/8TSTg=="}
    archive = fetch(dist["tarball"], DOWNLOADS / "ffprobe-win64-5.1.0.tgz", sha512=dist["integrity"].split("-", 1)[1])
    target.parent.mkdir(exist_ok=True)
    with tarfile.open(archive) as tf:
        member = next(m for m in tf.getmembers() if m.name.endswith("ffprobe.exe") and m.isfile())
        with tf.extractfile(member) as src, target.open("wb") as out:
            shutil.copyfileobj(src, out)
    return {"ffprobe": str(target), "ffprobe_package": "@ffprobe-installer/win32-x64 5.1.0"}

def socai() -> dict:
    target = ROOT / ".runtime/socai/socai.exe"
    if target.exists():
        return {"socai": str(target)}
    original_url = "https://github.com/socai-io/socai/releases/download/v0.6.9/socai-cli-windows-x86_64.zip"
    prefix = os.environ.get("MIXCUT_DOWNLOAD_PROXY", "").rstrip("/")
    url = prefix + "/" + original_url if prefix else original_url
    archive = fetch(url, DOWNLOADS / "socai-cli-v0.6.9.zip", size=31990144, sha256="7888be9762304ab9b7865f2267e4548376fa6c41f3e6b08c3cb6242d2e034b01")
    target.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        name = next(x for x in zf.namelist() if Path(x).name == "socai.exe")
        with zf.open(name) as src, target.open("wb") as out:
            shutil.copyfileobj(src, out)
    return {"socai": str(target), "socai_version": "v0.6.9"}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--with-collector", action="store_true", help="Also download the optional socai collection CLI")
    args = parser.parse_args()
    runtime_file = ROOT / ".runtime/runtime.json"
    runtime_file.parent.mkdir(parents=True, exist_ok=True)
    runtime = json.loads(runtime_file.read_text()) if runtime_file.exists() else {}
    runtime["python"] = sys.executable
    errors = []
    functions = [ffmpeg, ffprobe] + ([socai] if args.with_collector else [])
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(functions)) as pool:
        tasks = {pool.submit(function): function.__name__ for function in functions}
        for task in concurrent.futures.as_completed(tasks):
            try:
                runtime.update(task.result())
                runtime_file.write_text(json.dumps(runtime, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception as exc:
                errors.append(f"{tasks[task]}: {exc}")
                print(errors[-1], file=sys.stderr, flush=True)
    runtime_file.write_text(json.dumps(runtime, ensure_ascii=False, indent=2), encoding="utf-8")
    launcher = '@echo off\r\nset PYTHONIOENCODING=utf-8\r\n"' + sys.executable + '" "%~dp0..\\run.py" %*\r\n'
    (ROOT / ".runtime/python.cmd").write_text(launcher, encoding="utf-8")
    if errors:
        raise SystemExit(1)

if __name__ == "__main__":
    main()
