from __future__ import annotations
from collections import Counter
from pathlib import Path
import random
from .common import Project, PipelineError, ROLES, file_hash, object_hash, now, read_json, write_json, video_duration

def clip_pools(project: Project) -> dict[str, list[dict]]:
    pools = {}
    for role, folder in ROLES.items():
        items = []
        for path in sorted((project.clips / folder).glob("*.mp4")):
            if ".partial." in path.name:
                continue
            metadata_file = path.with_suffix(".clip.json")
            item = read_json(metadata_file)
            if item is None:
                # A user placing a file in a named role folder is an explicit manual classification.
                digest = file_hash(path)
                item = {"id": digest[:20], "source_sha256": digest, "source_path": str(path), "start": 0.0, "end": video_duration(path), "method": "manual_folder", "product": project.keyword}
            if item.get("product", project.keyword) != project.keyword:
                continue
            # Moving a low-confidence clip from 待确认 into a role folder confirms its role.
            item.update({"role": role, "approved": True, "path": str(path.resolve()), "duration": video_duration(path), "file_sha256": file_hash(path)})
            if item["duration"] <= 0:
                continue
            write_json(metadata_file, item)
            items.append(item)
        pools[role] = items
    return pools

def combination_signature(clips: list[dict]) -> str:
    return object_hash([{ "source": c["source_sha256"], "start": round(float(c["start"]), 3), "end": round(float(c["end"]), 3) } for c in clips])

def valid_combination(clips: list[dict], max_source_uses: int) -> bool:
    counts = Counter(c["source_sha256"] for c in clips)
    if max_source_uses and any(v > max_source_uses for v in counts.values()):
        return False
    for i, a in enumerate(clips):
        for b in clips[i+1:]:
            if a["source_sha256"] == b["source_sha256"] and min(a["end"], b["end"]) - max(a["start"], b["start"]) > .05:
                return False
    return True

def make_plans(project: Project, count: int | None = None) -> list[dict]:
    from .folder_mix import is_folder_mode, make_folder_plans
    if is_folder_mode(project):
        return make_folder_plans(project, count)
    cfg = project.config["mix"]
    count = int(count or cfg.get("count", 1))
    if not 1 <= count <= 1000:
        raise PipelineError("每批生成数量需在 1–1000 之间。")
    pools = clip_pools(project)
    roles = []
    for role in cfg["structure"]:
        if role not in ROLES:
            raise PipelineError("结构中有未知类别：" + role)
        if not pools[role]:
            if role in cfg.get("optional_roles", []):
                continue
            raise PipelineError(f"缺少“{ROLES[role]}”片段。请完成 AI 分类或将审核过的片段放入对应文件夹。")
        roles.append(role)
    if not roles:
        raise PipelineError("没有可用的混剪结构。")
    existing = [read_json(p) for p in sorted(project.plans.glob("*.json"))]
    used = {p["signature"] for p in existing if p}
    rng = random.Random(int(cfg.get("random_seed", 0)) + len(used))
    created = []
    for _ in range(max(1000, count * 500)):
        chosen = [rng.choice(pools[role]) for role in roles]
        if not valid_combination(chosen, int(cfg.get("max_source_uses_per_video", 2))):
            continue
        signature = combination_signature(chosen)
        if signature in used:
            continue
        used.add(signature)
        created.append({"schema_version": 1, "id": signature[:16], "signature": signature, "product": project.keyword, "clips": chosen, "body_seconds": sum(c["duration"] for c in chosen), "render": project.config["render"].copy(), "created_at": now()})
        if len(created) == count:
            break
    if len(created) != count:
        raise PipelineError(f"当前素材只能找到 {len(created)} 个额外有效组合，少于要求的 {count} 个。没有保存本批计划；请减少数量、增加素材，或调整同源使用上限。")
    for plan in created:
        write_json(project.plans / f"{plan['id']}.json", plan)
    return created
