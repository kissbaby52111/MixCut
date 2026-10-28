from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
ROLES = {"hooks": "开头", "pain_points": "痛点", "selling_points": "卖点", "proof": "效果展示", "conversion": "购买引导", "outro": "结尾"}
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi"}

class PipelineError(RuntimeError):
    pass

class CommandError(PipelineError):
    def __init__(self, result: subprocess.CompletedProcess):
        self.stdout = result.stdout
        self.stderr = result.stderr
        self.returncode = result.returncode
        super().__init__(f"{Path(result.args[0]).name} 执行失败：\n{result.stderr[-1800:] or result.stdout[-1800:]}")

def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def read_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))

def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".mixcut-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as out:
            json.dump(data, out, ensure_ascii=False, indent=2)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)

def file_hash(path: Path) -> str:
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()

def object_hash(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

def load_env(path: Path = ROOT / ".env") -> None:
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key.strip()):
                os.environ.setdefault(key.strip(), value.strip().strip("\"'"))

def runtime_tool(name: str) -> str:
    runtime = read_json(ROOT / ".runtime/runtime.json", {})
    configured = runtime.get(name)
    if configured and Path(configured).is_file():
        return configured
    candidates = {
        "ffmpeg": ROOT / ".runtime/ffmpeg/ffmpeg.exe",
        "ffprobe": ROOT / ".runtime/ffmpeg/ffprobe.exe",
        "socai": ROOT / ".runtime/socai/socai.exe",
    }
    if name in candidates and candidates[name].exists():
        return str(candidates[name])
    found = shutil.which(name)
    if found:
        return found
    raise PipelineError(f"缺少 {name}。请运行 setup.bat 下载本地运行组件。")

def run_command(args: list[str], *, timeout: int | None = None, env=None) -> subprocess.CompletedProcess:
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    # Files avoid an inherited stdout pipe keeping communicate() alive after a
    # background daemon outlives its CLI parent (notably socai on Windows).
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        process = subprocess.Popen([str(x) for x in args], stdout=stdout, stderr=stderr, env=env, creationflags=flags)
        try:
            process.wait(timeout=timeout)
        except (KeyboardInterrupt, subprocess.TimeoutExpired) as exc:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            if isinstance(exc, subprocess.TimeoutExpired):
                raise PipelineError(f"{Path(args[0]).name} 调用超时，已停止等待；已完成文件会保留。采集器后台状态需在重试前检查。") from None
            raise
        stdout.seek(0)
        stderr.seek(0)
        out, err = stdout.read(), stderr.read()
    result = subprocess.CompletedProcess(args, process.returncode, out.decode("utf-8", errors="replace"), err.decode("utf-8", errors="replace"))
    if result.returncode:
        raise CommandError(result)
    return result

def media_info(path: Path) -> dict:
    result = run_command([runtime_tool("ffprobe"), "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)], timeout=60)
    return json.loads(result.stdout)

def video_duration(path: Path) -> float:
    info = media_info(path)
    value = info.get("format", {}).get("duration")
    if not value:
        value = next((s.get("duration") for s in info.get("streams", []) if s.get("codec_type") == "video"), 0)
    return float(value or 0)

def video_stream_duration(path: Path) -> float:
    """Use the playable video stream, excluding a longer audio/container tail."""
    import math
    from fractions import Fraction
    info = media_info(path)
    video = next((stream for stream in info.get("streams", []) if stream.get("codec_type") == "video"
        and not stream.get("disposition", {}).get("attached_pic")), None)
    if video is None:
        raise PipelineError("素材没有可播放的视频轨道：" + str(path))
    candidates = []
    try:
        candidates.append(float(Fraction(str(video["duration_ts"])) * Fraction(video["time_base"])))
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        pass
    candidates.extend([video.get("duration"), info.get("format", {}).get("duration")])
    for value in candidates:
        try:
            seconds = float(value)
        except (TypeError,ValueError):
            continue
        if math.isfinite(seconds) and seconds > 0:
            return seconds
    raise PipelineError("视频轨道时长无效：" + str(path))

def has_audio(path: Path) -> bool:
    return any(s.get("codec_type") == "audio" for s in media_info(path).get("streams", []))

class Project:
    def __init__(self, config_path: str | Path = ROOT / "config.json"):
        self.config_path = Path(config_path).resolve()
        self.config = read_json(self.config_path)
        if not isinstance(self.config, dict):
            raise PipelineError(f"找不到配置文件：{self.config_path}")
        self.root = self.config_path.parent
        workspace = Path(self.config.get("workspace", "workspace/双头染发膏"))
        self.path = (self.root / workspace).resolve()
        self.inbox = self.path / "01_待审核"
        self.approved = self.path / "02_审核通过"
        self.clips = self.path / "03_分类片段"
        self.assets = self.path / "04_商品与包装"
        self.plans = self.path / "05_组合计划"
        self.outputs = self.path / "06_成片"
        self.state = self.path / ".state"
        for p in [self.inbox, self.approved, self.clips, self.assets, self.plans, self.outputs, self.state]:
            p.mkdir(parents=True, exist_ok=True)
        for key, label in ROLES.items():
            (self.clips / label).mkdir(exist_ok=True)
        (self.clips / "待确认").mkdir(exist_ok=True)

    def asset(self, value: str) -> Path:
        return (self.root / value).resolve()

    @property
    def keyword(self) -> str:
        return self.config["product"]["keyword"]

    def save_config(self) -> None:
        write_json(self.config_path, self.config)

    def approved_videos(self) -> list[Path]:
        return sorted(p for p in self.approved.rglob("*") if p.is_file() and p.suffix.lower() in VIDEO_EXTS)
