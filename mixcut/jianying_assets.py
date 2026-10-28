"""Searchable metadata and cloud-music serialization; no media downloading.

CloudMusicMaterial's type/music_id convention follows aoguai/pyJianYingDraft
(Apache-2.0, 69cee12ae3f7442416df16acdf368ae574764811). Music index provenance
and its MIT notice are retained under assets/jianying/.
"""
from __future__ import annotations
from functools import lru_cache
import math
import re
import uuid
from .common import ROOT, PipelineError, read_json


@lru_cache(maxsize=2)
def catalog(kind: str) -> list[dict]:
    if kind not in {"music", "effects"}:
        raise PipelineError("未知的剪映素材类型。")
    return read_json(ROOT / "assets/jianying" / (kind+".json"), [])


def native_settings(cfg: dict) -> dict:
    return {"music_source": "local" if cfg.get("music") else "none", "cloud_music": {},
        "effect_enabled": True, "effect_key": "流星雨", "draft_root": "", "selection_mode": "manual",
        "effect_categories": ["氛围", "动感"], "include_vip": False, "effect_count": 5, "effect_strength": 25,
        "effect_profile": "gentle_3_2", "face_cover_enabled": False, "local_music_index": "",
        **cfg.get("jianying", {})}


def validate_native(options: dict) -> dict:
    result = dict(options)
    if not isinstance(result.get("face_cover_enabled", False), bool):
        raise PipelineError("人物墨镜开关必须为布尔值。")
    if result.get("face_cover_enabled"):
        from .face_cover import face_cover_template
        face_cover_template()
    if result.get("selection_mode", "manual") not in {"auto", "manual"}:
        raise PipelineError("请选择自动包装或手动模式。")
    if result.get("selection_mode") == "auto":
        from .auto_packaging import validate_auto
        validate_auto(result)
        return result
    if result.get("music_source") not in {"local", "cloud", "none"}:
        raise PipelineError("请选择剪映配乐来源。")
    if result["music_source"] == "cloud":
        music = dict(result.get("cloud_music") or {})
        if not re.fullmatch(r"\d{6,30}", str(music.get("id", ""))):
            raise PipelineError("请从剪映曲库选择一首音乐，或将配乐来源改为“不加配乐”。")
        try:
            seconds = float(music.get("seconds", 0))
        except (TypeError,ValueError):
            seconds = 0
        if not math.isfinite(seconds) or not 0 < seconds <= 86400 or not str(music.get("name", "")).strip():
            raise PipelineError("云端音乐需要有效的名称和时长。")
        result["cloud_music"] = {"id": str(music["id"]), "name": str(music["name"]).strip(), "seconds": seconds}
    if result.get("effect_enabled"):
        keys = {effect["key"] for effect in catalog("effects")}
        if result.get("effect_key") not in keys:
            raise PipelineError("请从列表选择已收录的剪映特效。")
    return result


def cloud_audio_material(draft, music: dict):
    # An AudioMaterial subclass is required by the existing draft library, but
    # no file parser runs: JianYing resolves the music_id when opening the draft.
    class CloudAudio(draft.AudioMaterial):
        def __init__(self):
            self.material_id = uuid.uuid4().hex
            self.material_name = music["name"]
            self.path = "<cloud://" + music["id"] + ">"
            self.duration = round(music["seconds"]*1_000_000)

        def export_json(self):
            result = super().export_json()
            result.update(type="music", music_id=music["id"])
            return result
    return CloudAudio()
