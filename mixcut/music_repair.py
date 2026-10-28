"""Repair only playlists that reference music the client reported unavailable."""
from __future__ import annotations
import copy
import random
import re
import uuid

from .common import PipelineError, object_hash, read_json, write_json, now
from .auto_packaging import music_pool, frozen_packaging


def repair_music(project, unavailable_ids: list[str]) -> dict:
    if not unavailable_ids or any(not re.fullmatch(r"\d{6,30}", value) for value in unavailable_ids):
        raise PipelineError("请提供剪映明确报失效的音乐素材 ID。")
    state = read_json(project.state / "unavailable_music.json", {"ids": []})
    blocked = set(state["ids"]) | set(unavailable_ids)
    plans = [(path, read_json(path)) for path in sorted(project.plans.glob("*.json"))]
    automatic = [(path, plan) for path, plan in plans if plan.get("mode") == "folder_mix"
        and plan.get("render", {}).get("output_mode") == "jianying" and plan["render"].get("packaging_plan")]
    automatic = [(path, plan) for path, plan in automatic
        if not any(song.get("path") for song in plan["render"]["packaging_plan"].get("music", []))]
    # Restrict replacements to music already used by this batch. This is not an
    # online availability test: the caller supplies IDs reported by the client.
    current_ids = {song["id"] for _, plan in automatic for song in plan["render"]["packaging_plan"]["music"]}
    pool = [song for song in music_pool() if song["id"] in current_ids and song["id"] not in blocked]
    changes = []
    statuses = read_json(project.state / "render_status.json", {})
    for path, original in automatic:
        if statuses.get(original["id"], {}).get("status") == "complete":
            continue
        frozen = frozen_packaging(original, original["render"])
        if not blocked.intersection(song["id"] for song in frozen["music"]):
            continue
        if sum(round(song["seconds"]*1_000_000) for song in pool) < frozen["duration_us"]:
            raise PipelineError("本批其它配乐总时长不足，尚未修改计划；请补充可用曲库。")
        plan = copy.deepcopy(original)
        packaging = plan["render"]["packaging_plan"]
        seed = object_hash({"plan": plan["id"], "previous": packaging["seed"], "blocked": sorted(blocked), "pool": pool})
        order = list(pool);random.Random(seed).shuffle(order)
        playlist, cursor = [], packaging["start_us"]
        end = cursor + packaging["duration_us"]
        for song in order:
            length = min(round(song["seconds"]*1_000_000), end-cursor)
            if length <= 0:break
            playlist.append({**song, "source_start_us": 0, "start_us": cursor, "duration_us": length})
            cursor += length
        packaging.update(music=playlist, music_repair_seed=seed)
        frozen_packaging(plan, plan["render"])
        changes.append((path, original, plan))
    backup = project.state / "backups" / f"music_repair_{uuid.uuid4().hex[:12]}"
    if changes:
        for path, original, _ in changes:write_json(backup / path.name, original)
        write_json(backup / "render_status.json", statuses)
    for path, _, plan in changes:
        write_json(path, plan)
        if statuses.get(plan["id"], {}).get("status") != "complete":
            statuses[plan["id"]] = {"status": "pending", "note": "失效配乐已重新组合，请重建草稿。"}
    if changes:write_json(project.state / "render_status.json", statuses)
    write_json(project.state / "unavailable_music.json", {"ids": sorted(blocked), "time": now(),
        "basis": "IDs explicitly reported missing by this JianYing client; not a global availability claim"})
    report = {"changed_plans": [p["id"] for _, _, p in changes], "count": len(changes),
        "unavailable_ids": sorted(blocked), "backup": str(backup) if changes else None,
        "video_images_effects_preserved": True, "time": now()}
    write_json(project.state / "last_music_repair.json", report)
    return report
