"""Reconcile sub-frame metadata differences without changing playback speed."""
from __future__ import annotations
import copy
from .common import PipelineError

MAX_DURATION_DIFFERENCE_US = 100_000


def bounded_source_duration(requested_us: int, available_us: int, name: str) -> int:
    if requested_us <= 0 or available_us <= 0:
        raise PipelineError("素材时长必须大于零：" + name)
    if requested_us <= available_us:
        return requested_us
    if requested_us-available_us > MAX_DURATION_DIFFERENCE_US:
        raise PipelineError(f"片段“{name}”计划时长 {requested_us/1_000_000:g} 秒，"
            f"但视频轨只报告 {available_us/1_000_000:g} 秒，差异超过 0.1 秒；请检查素材并重新生成计划。")
    return available_us


def shift_packaging_start(packaging: dict | None, start_us: int) -> dict | None:
    if packaging is None:
        return None
    value = copy.deepcopy(packaging)
    delta = start_us-value["start_us"]
    value["start_us"] = start_us
    for item in [*value["effects"], *value["music"]]:
        item["start_us"] += delta
    return value
