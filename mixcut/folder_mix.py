"""Compose already prepared folders without collection, review or AI calls."""
from __future__ import annotations
import copy
import math
from pathlib import Path
import random
from .common import Project, PipelineError, VIDEO_EXTS, file_hash, object_hash, now, read_json, write_json, video_stream_duration
from .tail_packaging import folder_render_config
from .auto_packaging import attach_auto_packaging

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
DEFAULT_LABELS = ["开头", "开头转化点", "达人介绍", "明星产品介绍", "收尾转化点"]


def settings(project: Project) -> dict:
    default = {"slots": [{"id": f"slot_{i}", "label": label,
        "folder": str(project.path / "09_拼接素材" / f"{i:02d}_{label}")}
        for i, label in enumerate(DEFAULT_LABELS, 1)],
        "image_folder": str(project.path / "09_拼接素材/06_产品图"),
        "image_count": 4, "image_seconds": 300}
    return {**default, **copy.deepcopy(project.config.get("folder_mix", {}))}


def is_folder_mode(project: Project) -> bool:
    return project.config.get("workflow", "ai_pipeline") == "folder_mix"


def files_in(project: Project, folder: str, extensions: set[str]) -> list[Path]:
    if not folder:
        return []
    path = project.asset(folder)
    return sorted(p for p in path.glob("*") if p.is_file() and p.suffix.lower() in extensions and ".partial." not in p.name)


def create_default_folders(project: Project) -> None:
    """Create only the managed defaults; never create arbitrary mistyped external paths."""
    defaults = copy.deepcopy(project.config.get("folder_mix"))
    project.config.pop("folder_mix", None)
    try:
        cfg = settings(project)
    finally:
        if defaults is not None:
            project.config["folder_mix"] = defaults
    for value in [*(slot["folder"] for slot in cfg["slots"]), cfg["image_folder"]]:
        project.asset(value).mkdir(parents=True, exist_ok=True)


def folder_status(project: Project) -> dict:
    cfg = settings(project)
    return {"slots": [{**slot, "files": len(files_in(project, slot.get("folder", ""), VIDEO_EXTS))} for slot in cfg["slots"]],
        "images": len(files_in(project, cfg.get("image_folder", ""), IMAGE_EXTS)),
        "image_count": cfg.get("image_count", 4), "image_seconds": cfg.get("image_seconds", 300)}


def scan_inputs(project: Project) -> tuple[list[list[dict]], list[dict]]:
    cfg = settings(project)
    if not cfg["slots"]:
        raise PipelineError("请至少添加一个视频段落。")
    pools = []
    # Keep the previous container-duration cache intact, but never reuse it here.
    duration_cache = read_json(project.state / "folder-video-duration-v2.json", {})
    for slot in cfg["slots"]:
        paths = files_in(project, slot.get("folder", ""), VIDEO_EXTS)
        if not paths:
            raise PipelineError(f"“{slot['label']}”文件夹没有视频：{slot.get('folder', '')}")
        print(f"读取“{slot['label']}”：{len(paths)} 个视频", flush=True)
        items, seen = [], set()
        for path in paths:
            digest = file_hash(path)
            if digest in seen:
                continue
            seen.add(digest)
            duration = duration_cache.get(digest)
            if duration is None:
                duration = video_stream_duration(path)
                duration_cache[digest] = duration
            if not math.isfinite(duration) or duration <= 0:
                raise PipelineError("视频时长无效：" + str(path))
            items.append({"id": digest[:20], "source_sha256": digest, "file_sha256": digest,
                "source_path": str(path.resolve()), "path": str(path.resolve()), "start": 0., "end": duration,
                "duration": duration, "role": slot["id"], "label": slot["label"], "method": "prepared_folder", "approved": True})
        pools.append(items)
    write_json(project.state / "folder-video-duration-v2.json", duration_cache)
    images, seen = [], set()
    for path in files_in(project, cfg.get("image_folder", ""), IMAGE_EXTS):
        digest = file_hash(path)
        if digest not in seen:
            try:
                from PIL import Image
                with Image.open(path) as image:
                    image.verify()
            except (OSError, ValueError) as error:
                raise PipelineError(f"产品图无法读取，请检查文件：{path}") from error
            images.append({"path": str(path.resolve()), "sha256": digest, "seconds": float(cfg["image_seconds"])})
            seen.add(digest)
    needed = int(cfg.get("image_count", 4))
    if needed != 4:
        raise PipelineError("当前流程每条成片需要抽取 4 张产品图。")
    if len(images) < needed:
        raise PipelineError(f"产品图文件夹只有 {len(images)} 张内容不同的图片，需要至少 4 张；同图改名不会重复入选。")
    if not 0 < float(cfg["image_seconds"]) <= 3600:
        raise PipelineError("每张产品图时长应在 0–3600 秒之间。")
    return pools, images


def unrank_images(pool: list[dict], rank: int, count: int = 4) -> list[dict]:
    remaining, selected = list(pool), []
    for index in range(count):
        block = math.perm(len(remaining)-1, count-index-1)
        position, rank = divmod(rank, block)
        selected.append(remaining.pop(position))
    return selected


def make_folder_plans(project: Project, count: int | None = None) -> list[dict]:
    count = int(count if count is not None else project.config["mix"].get("count", 1))
    if not 1 <= count <= 1000:
        raise PipelineError("每批生成数量需在 1–1000 之间。")
    pools, images = scan_inputs(project)
    image_capacity = math.perm(len(images), 4)
    capacity = math.prod(len(pool) for pool in pools) * image_capacity
    existing = [read_json(path) for path in project.plans.glob("*.json")]
    used = {plan["signature"] for plan in existing if plan and plan.get("mode") == "folder_mix"}
    rng = random.Random(int(project.config["mix"].get("random_seed", 0)) + len(used))
    start = rng.randrange(capacity)
    step = rng.randrange(1, capacity+1)
    while math.gcd(step, capacity) != 1:
        step = rng.randrange(1, capacity+1)
    created = []
    for index in range(min(capacity, max(100000, count*1000))):
        video_rank, image_rank = divmod((start+index*step) % capacity, image_capacity)
        chosen = []
        for pool in pools:
            video_rank, position = divmod(video_rank, len(pool))
            chosen.append(pool[position])
        # The same complete video should not appear twice within one output.
        if len({clip["file_sha256"] for clip in chosen}) != len(chosen):
            continue
        selected_images = unrank_images(images, image_rank)
        signature = object_hash({"mode": "folder_mix-v1", "videos": [c["file_sha256"] for c in chosen],
            "images": [image["sha256"] for image in selected_images]})
        if signature in used:
            continue
        used.add(signature)
        render = folder_render_config(project.config["render"])
        render.update(product_images=copy.deepcopy(selected_images), product_image=selected_images[0]["path"],
            tail_seconds=sum(image["seconds"] for image in selected_images))
        created.append({"schema_version": 2, "mode": "folder_mix", "id": signature[:16], "signature": signature,
            "product": project.keyword, "clips": copy.deepcopy(chosen), "body_seconds": sum(c["duration"] for c in chosen),
            "render": render, "created_at": now()})
        attach_auto_packaging(project, created[-1])
        from .corner_overlay import attach_corner_overlay
        attach_corner_overlay(project,created[-1])
        if len(created) == count:
            break
    if len(created) != count:
        raise PipelineError(f"本轮找到 {len(created)} 个额外有效组合，少于要求的 {count} 个；没有保存本批计划。请减少数量或增加不同的视频/图片。")
    from .plan_batches import new_batch_id, record_batch
    batch_id = new_batch_id()
    for plan in created:
        plan["batch_id"] = batch_id
        write_json(project.plans / f"{plan['id']}.json", plan)
    record_batch(project,[p["id"] for p in created],batch_id=batch_id,created_at=created[0]["created_at"])
    print(f"已生成新批次 {batch_id}：{len(created)} 条；续跑和复合草稿默认仅处理本批。",flush=True)
    return created
