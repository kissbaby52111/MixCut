"""Enforce the prepared-video / product-image packaging boundary."""
from __future__ import annotations
import copy

PACKAGING_VERSION = "tail-only-v1"


def folder_render_config(config: dict) -> dict:
    cfg = copy.deepcopy(config)
    cfg["packaging_version"] = PACKAGING_VERSION
    cfg["original_audio_volume"] = 1.0
    cfg["music_scope"] = "tail"
    cfg.setdefault("meteor", {})["scope"] = "tail"
    return cfg


def effective_render_config(plan: dict) -> dict:
    if plan.get("mode") == "folder_mix":
        return folder_render_config(plan["render"])
    return copy.deepcopy(plan["render"])
