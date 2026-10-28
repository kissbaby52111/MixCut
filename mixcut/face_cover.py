"""Native face-cover effect, independent of the five product-image effects."""
from __future__ import annotations
import copy
import uuid
from .common import ROOT, PipelineError, read_json

TEMPLATE_PATH = ROOT / "assets/jianying/face_cover.json"
# Materials the client counts as paid benefits.  Exporting a draft that uses one
# without the matching entitlement fails with 10004 ("material lost or no
# permission or corrupted") and the frame falls back to a default image.
SVIP_RESOURCE_IDS = {"7536096426359770392"}


def face_cover_template() -> dict:
    value = read_json(TEMPLATE_PATH, {})
    if value.get("name") != "拽酷墨镜" or not value.get("material", {}).get("resource_id"):
        raise PipelineError("尚未取得‘拽酷墨镜’原生资源，请先完成独立墨镜测试；关闭开关可继续生成草稿。")
    return value


def append_face_cover(content: dict, options: dict, opening_us: int, total_us: int) -> dict:
    if not options.get("face_cover_enabled", False):
        return {"enabled": False}
    template = face_cover_template()
    if total_us <= opening_us:
        return {"enabled": True, "applied": False, "reason": "没有开头之后的内容"}
    material = copy.deepcopy(template["material"])
    material.update(id=uuid.uuid4().hex, apply_target_type=2, name="拽酷墨镜")
    for key in ["path", "request_id", "algorithm_artifact_path"]:
        material.pop(key, None)
    segment = copy.deepcopy(template["segment"])
    segment.update(id=uuid.uuid4().hex, material_id=material["id"],
        target_timerange={"start": opening_us, "duration": total_us-opening_us},
        common_keyframes=[], keyframe_refs=[])
    segment.pop("source_timerange", None)
    segment["extra_material_refs"] = []
    content["materials"].setdefault("video_effects", []).append(material)
    content["tracks"].append({"id":uuid.uuid4().hex,"type":"effect","attribute":0,"flag":0,
        "name":"人物挡脸_拽酷墨镜_跳过开头","is_default_name":False,"segments":[segment]})
    return {"enabled":True,"applied":True,"name":"拽酷墨镜","resource_id":material["resource_id"],
        "client_commercial_label":template.get("client_commercial_label","未核验"),
        "start_seconds":opening_us/1_000_000,"duration_seconds":(total_us-opening_us)/1_000_000,
        "scope":"after_first_video_to_end","client_tracking_verified":False,
        "requires_svip":material["resource_id"] in SVIP_RESOURCE_IDS,
        "export_blocked_without_svip":True}
