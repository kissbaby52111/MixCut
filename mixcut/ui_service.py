"""Small, testable operations used by the native desktop interface."""
from __future__ import annotations
import copy
import json
import math
import os
from pathlib import Path
import queue
import re
import signal
import subprocess
import sys
import tempfile
import threading
from urllib.parse import urlparse

from .common import ROOT, ROLES, VIDEO_EXTS, PipelineError, Project, object_hash, read_json, write_json
from .cli import project_lock, summary

MATERIAL_FOLDERS = {"待审核": "inbox", "已通过": "approved", "已淘汰": "rejected"}

def safe_product_folder(keyword: str) -> str:
    clean = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", keyword).strip(" .")[:64]
    if not clean:
        raise PipelineError("商品关键词不能为空。")
    if clean.upper() in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1,10)], *[f"LPT{i}" for i in range(1,10)]}:
        clean = "商品_" + clean
    if clean != keyword:
        clean += "_" + object_hash(keyword)[:6]
    return clean

def number(value, label: str, minimum: float, maximum: float, *, integer=False):
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise PipelineError(f"{label}应填写数字。") from None
    if not math.isfinite(result) or not minimum <= result <= maximum or (integer and not result.is_integer()):
        raise PipelineError(f"{label}应在 {minimum:g}–{maximum:g} 之间" + ("，且为整数。" if integer else "。"))
    return int(result) if integer else result

def read_env_values(path: Path) -> dict[str, str]:
    values = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip("\"'")
    return values

def save_secrets(path: Path, changes: dict[str, str]) -> None:
    changes = {key: value.strip() for key, value in changes.items() if value.strip()}
    if not changes:
        return
    for key, value in changes.items():
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) or "\n" in value or "\r" in value:
            raise PipelineError("密钥格式不正确，请填写单行内容。")
    lines = path.read_text(encoding="utf-8-sig").splitlines() if path.exists() else []
    result, written = [], set()
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else ""
        if key in changes:
            if key not in written:
                result.append(f"{key}={changes[key]}")
                written.add(key)
        else:
            result.append(line)
    result.extend(f"{key}={value}" for key, value in changes.items() if key not in written)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".mixcut-env-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as file:
            file.write("\n".join(result) + "\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    os.environ.update(changes)

class UiService:
    def __init__(self, config_path: str | Path, env_path: Path | None = None):
        self.config_path = Path(config_path).resolve()
        self.env_path = env_path or ROOT / ".env"
        self.reload()

    def reload(self) -> Project:
        self.project = Project(self.config_path)
        self.rejected = self.project.path / "00_已淘汰"
        return self.project

    def status(self) -> dict:
        return summary(self.reload())

    def save_basic(self, keyword: str, limit, count) -> bool:
        keyword = keyword.strip()
        if not keyword or len(keyword) > 100:
            raise PipelineError("请输入 1–100 字的商品关键词。")
        limit = number(limit, "采集数量", 1, 100, integer=True)
        count = number(count, "出片数量", 1, 1000, integer=True)
        project = self.reload()
        changed = project.keyword != keyword
        with project_lock(project):
            config = copy.deepcopy(project.config)
            config["product"].update({"name": keyword, "keyword": keyword})
            config["collection"]["limit"] = limit
            config["mix"]["count"] = count
            if changed:
                config["workspace"] = "workspace/" + safe_product_folder(keyword)
                config["render"]["product_image"] = ""
                config["render"]["music"] = ""
            write_json(self.config_path, config)
        self.reload()
        return changed

    def save_render(self, values: dict) -> None:
        project = self.reload()
        with project_lock(project):
            config = copy.deepcopy(project.config)
            render = config["render"]
            width = number(values["width"], "宽度", 64, 3840, integer=True)
            height = number(values["height"], "高度", 64, 3840, integer=True)
            if width % 2 or height % 2:
                raise PipelineError("视频宽度和高度需要是偶数。")
            render.update({"width": width, "height": height, "fps": number(values["fps"], "帧率", 1, 60, integer=True), "tail_seconds": number(values["tail_seconds"], "尾图时长", 1, 3600), "music_volume": number(values["music_volume"], "音乐音量", 0, 1), "original_audio_volume": number(values["original_audio_volume"], "原声音量", 0, 1)})
            for name, label in [("product_image", "商品图"), ("music", "背景音乐"), ("effect_video", "自定义特效")]:
                value = str(values.get(name, "")).strip()
                if value and not project.asset(value).is_file():
                    raise PipelineError(f"{label}文件不存在，请重新选择。")
                render[name] = value
            scope = values.get("scope", "all")
            if scope not in {"all", "body", "tail"}:
                raise PipelineError("请选择特效作用范围。")
            render.setdefault("meteor", {}).update({"enabled": bool(values.get("meteor_enabled")), "scope": scope, "intensity": number(values["intensity"], "特效强度", 0, 1)})
            write_json(self.config_path, config)
        self.reload()

    def save_ai(self, *, base_url: str, model: str, api_key: str = "", asr_url: str = "", asr_model: str = "whisper-1", asr_key: str = "") -> None:
        def url(value):
            value = value.strip().rstrip("/")
            if value:
                parsed = urlparse(value)
                if parsed.scheme not in {"https", "http"} or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
                    raise PipelineError("服务地址应为 http(s) 地址，不要在地址中填写密钥或账号。")
            return value
        base_url, asr_url = url(base_url), url(asr_url)
        model, asr_model = model.strip(), asr_model.strip()
        if bool(base_url) != bool(model):
            raise PipelineError("视觉服务地址和模型名称需同时填写，或同时留空。")
        if asr_url and not asr_model:
            raise PipelineError("请填写转写模型名称。")
        project = self.reload()
        with project_lock(project):
            config = copy.deepcopy(project.config)
            cfg = config["analysis"]
            cfg.update({"base_url": base_url, "model": model})
            transcription = cfg.setdefault("transcription", {})
            transcription.update({"base_url": asr_url, "model": asr_model or "whisper-1"})
            changes = {cfg.get("api_key_env", "MIXCUT_API_KEY"): api_key, transcription.get("api_key_env", "MIXCUT_ASR_KEY"): asr_key}
            save_secrets(self.env_path, changes)
            write_json(self.config_path, config)
        self.reload()

    def secret_values(self) -> list[str]:
        values = read_env_values(self.env_path)
        cfg = self.project.config["analysis"]
        keys = [cfg.get("api_key_env", "MIXCUT_API_KEY"), cfg.get("transcription", {}).get("api_key_env", "MIXCUT_ASR_KEY")]
        return [value for key in keys if (value := os.environ.get(key) or values.get(key))]

    def material_rows(self, category="待审核") -> list[dict]:
        folder = {"待审核": self.project.inbox, "已通过": self.project.approved, "已淘汰": self.rejected}[category]
        validation = read_json(self.project.state / "collection/media_validation.json", [])
        known = {Path(x["file"]).name: x for x in validation}
        rows = []
        for file in sorted(folder.glob("*")):
            if not file.is_file() or file.suffix.lower() not in VIDEO_EXTS or ".partial." in file.name:
                continue
            try:
                meta = read_json(file.with_suffix(".source.json"), {})
            except (ValueError, OSError):
                meta = {}
            meta = {**known.get(file.name, {}), **meta}
            rows.append({"path": file, "name": file.name, "title": meta.get("title") or meta.get("video_id") or file.stem, "duration": meta.get("duration"), "size": file.stat().st_size, "source": meta.get("url", ""), "category": category})
        return rows

    def clip_rows(self, category="全部分类") -> list[dict]:
        rows = []
        labels = [*ROLES.values(), "待确认"] if category == "全部分类" else [category]
        for label in labels:
            if label not in {*ROLES.values(), "待确认"}:
                continue
            for file in sorted((self.project.clips / label).glob("*.mp4")):
                if ".partial." in file.name:
                    continue
                try:
                    meta = read_json(file.with_suffix(".clip.json"), {})
                except (ValueError, OSError):
                    meta = {}
                rows.append({"path": file, "name": file.name, "category": label, "duration": meta.get("duration"), "source": Path(meta.get("source_path", "")).name, "description": (meta.get("analysis") or {}).get("description", ""), "approved": label != "待确认"})
        return rows

    def plan_rows(self) -> list[dict]:
        status = read_json(self.project.state / "render_status.json", {})
        rows = []
        for file in sorted(self.project.plans.glob("*.json")):
            try:
                plan = read_json(file)
            except (ValueError, OSError):
                continue
            rows.append({"id": plan["id"], "body_seconds": plan["body_seconds"], "tail_seconds": plan["render"]["tail_seconds"], "output_mode": plan["render"].get("output_mode", "ffmpeg"), "status": status.get(plan["id"], {}).get("status", "pending"), "output": status.get(plan["id"], {}).get("path", "")})
        return rows

    def move_materials(self, paths: list[Path], target_category: str) -> int:
        folders = {"待审核": self.project.inbox, "已通过": self.project.approved, "已淘汰": self.rejected}
        if target_category not in folders:
            raise PipelineError("未知审核状态。")
        return self._move(paths, folders[target_category], folders.values())

    def move_clips(self, paths: list[Path], label: str) -> int:
        if label not in {*ROLES.values(), "待确认"}:
            raise PipelineError("请选择片段类别。")
        folders = [self.project.clips / x for x in [*ROLES.values(), "待确认"]]
        return self._move(paths, self.project.clips / label, folders, clip_label=label)

    def _move(self, paths, destination, allowed, clip_label=None) -> int:
        if not paths:
            raise PipelineError("请先选择文件。")
        allowed = [folder.resolve() for folder in allowed]
        destination = destination.resolve()
        if not destination.is_relative_to(self.project.path.resolve()):
            raise PipelineError("目标目录超出当前商品目录。")
        operations, mapping = [], {}
        for path in dict.fromkeys(Path(p).resolve() for p in paths):
            if not path.is_file() or not any(path.is_relative_to(folder) for folder in allowed):
                raise PipelineError("只能操作当前列表中的素材文件。")
            target = destination / path.name
            if path == target:
                continue
            for source in [path, path.with_suffix(".source.json"), path.with_suffix(".srt"), path.with_suffix(".clip.json")]:
                if source.exists():
                    dest = destination / source.name
                    if dest.exists():
                        raise PipelineError(f"目标中已有同名文件，未覆盖：{dest.name}")
                    operations.append((source, dest))
            mapping[str(path)] = str(target)
        with project_lock(self.project):
            destination.mkdir(parents=True, exist_ok=True)
            completed = []
            try:
                for source, dest in operations:
                    source.rename(dest)
                    completed.append((source, dest))
            except OSError:
                for source, dest in reversed(completed):
                    if dest.exists() and not source.exists():
                        dest.rename(source)
                raise
            for old, new in mapping.items():
                for suffix in [".source.json", ".clip.json"]:
                    meta_path = Path(new).with_suffix(suffix)
                    if meta_path.exists():
                        item = read_json(meta_path, {})
                        item["path"] = new
                        if suffix == ".clip.json" and clip_label:
                            item["approved"] = clip_label != "待确认"
                            if clip_label in ROLES.values():
                                item["role"] = next(key for key,value in ROLES.items() if value == clip_label)
                        write_json(meta_path, item)
            for plan_path in self.project.plans.glob("*.json"):
                plan = read_json(plan_path)
                changed = False
                for clip in plan.get("clips", []):
                    if clip.get("path") in mapping:
                        clip["path"] = mapping[clip["path"]]
                        changed = True
                if changed:
                    write_json(plan_path, plan)
            state_path = self.project.state / "sources.json"
            sources = read_json(state_path, {})
            for item in sources.values():
                if item.get("path") in mapping:
                    item["path"] = mapping[item["path"]]
            if sources:
                write_json(state_path, sources)
        return len(mapping)

    def apply_render_to_pending(self) -> int:
        project = self.reload()
        count = 0
        status = read_json(project.state / "render_status.json", {})
        with project_lock(project):
            for file in project.plans.glob("*.json"):
                plan = read_json(file)
                if status.get(plan["id"], {}).get("status") != "complete":
                    plan["render"] = copy.deepcopy(project.config["render"])
                    write_json(file, plan)
                    count += 1
        return count

class JobRunner:
    """One subprocess pipeline at a time; no Tk calls from the worker thread."""
    def __init__(self, config_path: Path, *, secrets=(), root: Path = ROOT):
        self.config_path = Path(config_path)
        self.root = root
        self.secrets = list(secrets)
        self.events: queue.Queue = queue.Queue()
        self.busy = False
        self.process: subprocess.Popen | None = None
        self.cancelled = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self, label: str, commands: list[list[str]]) -> None:
        if self.busy:
            raise PipelineError("已有任务正在运行，请等待完成或先停止当前任务。")
        self.busy = True
        self.cancelled.clear()
        self.thread = threading.Thread(target=self._work, args=(label, commands), daemon=True)
        self.thread.start()

    def _work(self, label, commands):
        code = 0
        try:
            runtime = read_json(ROOT / ".runtime/runtime.json", {})
            fallback_python = str(Path(sys.executable).with_name("python.exe")) if os.name == "nt" else sys.executable
            python = runtime.get("python") or fallback_python
            env = os.environ.copy()
            env.update({"PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"})
            for index, command in enumerate(commands, 1):
                if self.cancelled.is_set():
                    break
                self.events.put(("step", f"{label} · {index}/{len(commands)}"))
                args = [python, "-u", str(self.root / "run.py"), "--config", str(self.config_path), *command]
                options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
                with subprocess.Popen(args, cwd=self.root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding="utf-8", errors="replace", env=env, **options) as process:
                    self.process = process
                    if self.cancelled.is_set():
                        self.stop()
                    for line in process.stdout:
                        for secret in self.secrets:
                            if secret:
                                line = line.replace(secret, "[已隐藏密钥]")
                        self.events.put(("log", line.rstrip("\r\n")))
                    code = process.wait()
                self.process = None
                if code:
                    break
        except Exception as exc:
            code = 1
            message = str(exc)
            for secret in self.secrets:
                if secret:
                    message = message.replace(secret, "[已隐藏密钥]")
            self.events.put(("log", f"任务失败：{message}"))
        finally:
            self.process = None
            self.busy = False
            self.events.put(("done", {"label": label, "code": code, "cancelled": self.cancelled.is_set()}))

    def stop(self) -> None:
        self.cancelled.set()
        process = self.process
        if not process or process.poll() is not None:
            return
        def terminate():
            try:
                if os.name == "nt":
                    subprocess.run(["taskkill.exe", "/PID", str(process.pid), "/T", "/F"], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW, timeout=15)
                else:
                    os.killpg(process.pid, signal.SIGTERM)
            except (OSError, subprocess.SubprocessError):
                if process.poll() is None:
                    process.terminate()
        threading.Thread(target=terminate, daemon=True).start()
