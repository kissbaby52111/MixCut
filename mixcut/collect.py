from __future__ import annotations
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from .common import ROOT, Project, PipelineError, CommandError, VIDEO_EXTS, file_hash, now, read_json, run_command, runtime_tool, write_json, media_info

def socai_env() -> dict:
    env = os.environ.copy()
    home = ROOT / ".runtime/socai-home"
    home.mkdir(parents=True, exist_ok=True)
    config = home / "config.json"
    if not config.exists():
        write_json(config, {"chrome": {"profile": "managed", "profile_dir": str(home / "chrome-profile")}, "runs": {"dir": str(home / "runs")}})
    env["SOCAI_HOME"] = str(home)
    env["SOCAI_CONFIG_PATH"] = str(config)
    env["SOCAI_RUNS_DIR"] = str(home / "runs")
    return env

def extract_json(text: str):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        candidates = []
        for match in re.finditer(r"[\[{]", text):
            try:
                value, _ = decoder.raw_decode(text[match.start():])
                if isinstance(value, (dict, list)):
                    candidates.append(value)
            except json.JSONDecodeError:
                pass
        if not candidates:
            raise PipelineError("采集器未返回 JSON。请检查 Chrome 登录状态及采集日志。")
        return max(candidates, key=lambda x: len(json.dumps(x)))

def walk_objects(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk_objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_objects(child)

def save_local_video(project: Project, source: Path, metadata: dict | None = None) -> dict:
    source = source.resolve()
    if not source.is_file() or source.suffix.lower() not in VIDEO_EXTS:
        raise PipelineError(f"不是支持的视频文件：{source}")
    info = media_info(source)
    video = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    if not video:
        raise PipelineError(f"文件没有可用的视频轨道：{source}")
    digest = file_hash(source)
    state_file = project.state / "sources.json"
    state = read_json(state_file, {})
    if digest in state:
        return {**state[digest], "duplicate": True}
    target = project.inbox / f"dy_{digest[:16]}{source.suffix.lower()}"
    if source != target.resolve():
        shutil.copy2(source, target)
    item = {"id": digest[:16], "sha256": digest, "path": str(target), "imported_at": now(), "keyword": project.keyword, "duration": float(info.get("format", {}).get("duration", 0)), "width": video.get("width"), "height": video.get("height"), **(metadata or {})}
    state[digest] = item
    write_json(state_file, state)
    write_json(target.with_suffix(".source.json"), item)
    return item

def collect(project: Project, *, limit: int | None = None) -> dict:
    exe = runtime_tool("socai")
    cfg = project.config["collection"]
    limit = int(limit or cfg.get("limit", 12))
    if not 1 <= limit <= 100:
        raise PipelineError("单次采集数量需在 1–100 之间。")
    env = socai_env()
    wait = int(cfg.get("wait_seconds", 90))
    run_dir = project.state / "collection"
    run_dir.mkdir(exist_ok=True)
    print(f"在已登录的 Chrome 中搜索：{project.keyword}（上限 {limit}）", flush=True)
    try:
        result = run_command([exe, "dy", "search", project.keyword, "--num", str(limit), "--wait-seconds", str(wait)], env=env, timeout=wait + 90)
    except CommandError as exc:
        try:
            failed = extract_json(exc.stdout)
        except PipelineError:
            failed = {"error": str(exc)}
        write_json(run_dir / "search.json", failed)
        write_json(run_dir / "last_report.json", {"keyword": project.keyword, "stage": "search", "downloaded": 0, "status": "failed", "time": now(), "details": failed})
        if any(item.get("login_required") is True for item in walk_objects(failed)):
            raise PipelineError("抖音需要登录。请在专用 Chrome 完成扫码登录，保持窗口开启，然后重新运行 collect。") from None
        if any(item.get("challenge_required") is True for item in walk_objects(failed)):
            raise PipelineError("抖音需要人工验证。请在专用 Chrome 完成验证后重试；工具不会绕过验证。") from None
        raise
    payload = extract_json(result.stdout)
    write_json(run_dir / "search.json", payload)
    ids = []
    cards = {}
    for item in walk_objects(payload):
        video_id = str(item.get("video_id") or item.get("aweme_id") or "")
        if re.fullmatch(r"\d{8,30}", video_id) and video_id not in ids:
            ids.append(video_id)
            cards[video_id] = item
    if not ids:
        raise PipelineError("没有取得抖音作品 ID。搜索结果已保存在 .state/collection/search.json；请确认已登录并能看到搜索结果。")
    history = read_json(run_dir / "history.json", {})
    imported, skipped, errors = [], [], []
    for video_id in ids[:limit]:
        if history.get(video_id, {}).get("status") == "downloaded":
            skipped.append(video_id)
            continue
        print(f"下载作品 {video_id}", flush=True)
        try:
            before = {p.resolve() for p in (ROOT / ".runtime/socai-home").rglob("*") if p.is_file() and p.suffix.lower() in VIDEO_EXTS}
            response = run_command([exe, "dy", "get-videos", "--video", video_id, "--download-media", "--num-comments", "0", "--wait-seconds", str(wait)], env=env, timeout=wait + 180)
            data = extract_json(response.stdout)
            write_json(run_dir / f"{video_id}.json", data)
            paths = set()
            for obj in walk_objects(data):
                for key, value in obj.items():
                    if isinstance(value, str) and ("path" in key or key in {"file", "local_file"}):
                        candidate = Path(value)
                        if candidate.is_file() and candidate.suffix.lower() in VIDEO_EXTS and candidate.resolve().is_relative_to((ROOT / ".runtime/socai-home").resolve()):
                            paths.add(candidate.resolve())
            after = {p.resolve() for p in (ROOT / ".runtime/socai-home").rglob("*") if p.is_file() and p.suffix.lower() in VIDEO_EXTS}
            paths.update(after - before)
            if not paths:
                raise PipelineError("采集器没有返回本地视频文件；可能需要登录、作品不可下载或页面已变化。")
            card = cards.get(video_id, {})
            items = [save_local_video(project, p, {"platform": "douyin", "video_id": video_id, "url": f"https://www.douyin.com/video/{video_id}", "title": card.get("title") or card.get("description") or "", "author": card.get("author") or ""}) for p in sorted(paths)]
            imported.extend(items)
            history[video_id] = {"status": "downloaded", "items": items, "time": now()}
        except (PipelineError, TimeoutError, subprocess.TimeoutExpired) as exc:
            errors.append({"video_id": video_id, "error": str(exc)})
            history[video_id] = {"status": "failed", "error": str(exc), "time": now()}
        write_json(run_dir / "history.json", history)
    report = {"keyword": project.keyword, "downloaded": len(imported), "skipped": skipped, "errors": errors, "items": imported, "time": now()}
    write_json(run_dir / "last_report.json", report)
    if not imported and not skipped:
        raise PipelineError("本次未下载到视频。详情：" + str(run_dir / "last_report.json"))
    return report

def approve(project: Project, files: list[str], *, all_files: bool = False) -> int:
    selected = [p for p in project.inbox.iterdir() if p.suffix.lower() in VIDEO_EXTS] if all_files else [Path(p) if Path(p).is_absolute() else project.inbox / p for p in files]
    if not selected:
        raise PipelineError("未选择文件。审核后可将原片移入“02_审核通过”，或使用 approve 文件名。")
    for source in selected:
        source = source.resolve()
        if not source.is_relative_to(project.inbox.resolve()) or not source.is_file():
            raise PipelineError(f"只能批准待审核目录中的现有文件：{source}")
        target = project.approved / source.name
        if target.exists():
            raise PipelineError(f"目标已经存在，未覆盖：{target}")
        source.rename(target)
        sidecar = source.with_suffix(".source.json")
        if sidecar.exists():
            sidecar.rename(target.with_suffix(".source.json"))
        srt = source.with_suffix(".srt")
        if srt.exists():
            srt.rename(target.with_suffix(".srt"))
    return len(selected)
