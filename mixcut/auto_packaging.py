"""Freeze five full-tail effects and a non-overlapping random music playlist."""
from __future__ import annotations
import ast
import copy
from functools import lru_cache
import math
import random
import re
from .common import ROOT, PipelineError, object_hash, read_json
from .jianying_assets import catalog

AUTO_VERSION = "native-auto-v1"
GENTLE_PROFILE = "gentle_3_2"


@lru_cache(maxsize=1)
def effect_groups() -> dict:
    return read_json(ROOT / "assets/jianying/effect_groups.json")


def gentle_groups() -> dict:
    return read_json(ROOT / "assets/jianying/gentle_effect_groups.json")


def effect_pool(options: dict) -> list[dict]:
    groups = gentle_groups()["groups"] if options.get("effect_profile") == GENTLE_PROFILE else effect_groups()["groups"]
    selected = options.get("effect_categories", ["氛围", "动感"])
    if not isinstance(selected, list) or not selected or any(name not in groups for name in selected):
        raise PipelineError("自动特效只支持氛围、动感两个风格候选池，请至少选择一个。")
    indexed = {item["key"]: item for item in catalog("effects")}
    result, seen = [], set()
    for category in dict.fromkeys(selected):
        for key in groups[category]:
            item = indexed.get(key)
            if not item:
                raise PipelineError("风格候选池包含未收录的特效：" + key)
            if item["resource_id"] in seen or (item.get("vip") and not options.get("include_vip", False)):
                continue
            result.append({**item, "category": category})
            seen.add(item["resource_id"])
    return result


def music_pool() -> list[dict]:
    result, seen = [], set()
    for item in catalog("music"):
        try:
            seconds = float(item["seconds"])
        except (ValueError, TypeError, KeyError):
            continue
        identity = str(item.get("id", ""))
        if identity.isdigit() and identity not in seen and item.get("name") and math.isfinite(seconds) and 0 < seconds <= 86400:
            result.append({"id": identity, "name": item["name"], "seconds": seconds})
            seen.add(identity)
    return result


@lru_cache(maxsize=1)
def _parameter_metadata() -> dict:
    # Read literal upstream metadata without importing media/UI dependencies.
    tree = ast.parse((ROOT / "vendor/pyJianYingDraft/pyJianYingDraft/metadata/video_scene_effect.py").read_text(encoding="utf-8"))
    result = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        if not isinstance(node.value.func, ast.Name) or node.value.func.id != "EffectMeta":
            continue
        values = []
        if len(node.value.args) > 5:
            for param in node.value.args[5].elts:
                values.append([ast.literal_eval(arg) for arg in param.args])
        result[node.targets[0].id] = values
    return result


def mild_params(key: str, strength: float) -> list:
    result = []
    for name, default, minimum, maximum in _parameter_metadata().get(key, []):
        if maximum > minimum and any(word in name for word in ["强度", "氛围", "光量", "光晕", "发光", "模糊", "滤镜", "浓度", "密度"]):
            default_percent = 100 * (default-minimum)/(maximum-minimum)
            result.append(max(0., min(float(strength), default_percent, 100.)))
        else:
            result.append(None)
    return result


def effect_params(key: str, strength: float, profile: str = "legacy") -> list:
    # Preserve old frozen plans exactly; new curated plans understand upstream
    # English parameter names to control brightness, blur and camera motion.
    # Parameters without curated overrides retain their native defaults.
    if profile != GENTLE_PROFILE:
        return mild_params(key, strength)
    overrides = gentle_groups()["parameter_percent"].get(key)
    if overrides is None:
        raise PipelineError("该特效不在轻量候选池中：" + key)
    result = []
    for name, default, minimum, maximum in _parameter_metadata().get(key, []):
        percent = overrides.get(name)
        if percent is not None and any(token in name for token in ["intensity", "luminance", "filter", "blur", "distortion"]):
            percent = min(percent, float(strength))
        result.append(percent)
    return result


def validate_auto(options: dict) -> None:
    if options.get("music_source") not in {"cloud", "local_index"}:
        raise PipelineError("自动包装使用剪映云端曲库或本地曲库；如需单个本地文件或不加配乐，请切换手动模式。")
    if options.get("effect_count", 5) != 5:
        raise PipelineError("自动包装固定同时叠加 5 种不同特效。")
    if options.get("effect_profile", "legacy") not in {"legacy", GENTLE_PROFILE}:
        raise PipelineError("未知的自动特效方案。")
    if options.get("effect_profile") == GENTLE_PROFILE:
        if set(options.get("effect_categories", [])) != {"氛围", "动感"}:
            raise PipelineError("轻量方案固定抽取 3 种氛围 + 2 种动感，请同时启用两个候选池。")
        pool = effect_pool(options)
        if any(sum(item["category"] == name for item in pool) < count for name, count in [("氛围",3),("动感",2)]):
            raise PipelineError("轻量候选池至少需要 3 种氛围和 2 种动感。")
    try:
        strength = float(options.get("effect_strength", 25))
    except (TypeError, ValueError):
        strength = math.nan
    if not math.isfinite(strength) or not 0 <= strength <= 100:
        raise PipelineError("自动特效强度应为 0–100。")
    if len(effect_pool(options)) < 5:
        raise PipelineError("当前风格候选池不足 5 种不同特效，请调整风格或素材索引。")
    if options.get("music_source") == "cloud" and not music_pool():
        raise PipelineError("剪映音乐索引中没有可用的音乐时长数据。")


def attach_auto_packaging(project, plan: dict) -> None:
    cfg = plan["render"]
    if plan.get("mode") != "folder_mix" or cfg.get("output_mode") != "jianying":
        return
    from .jianying_assets import native_settings, validate_native
    options = validate_native(native_settings(cfg))
    if options.get("selection_mode") != "auto":
        cfg.pop("packaging_plan", None)
        return
    blocked = set(read_json(project.state / "unavailable_music.json", {}).get("ids", []))
    effects = effect_pool(options)
    if options.get("music_source") == "local_index":
        from .local_music import local_music_pool
        # Local files cannot expire, so the client-side blacklist is irrelevant.
        songs = local_music_pool(project, options)
    else:
        songs = [song for song in music_pool() if song["id"] not in blocked]
    policy = {key: options[key] for key in ["effect_categories", "include_vip", "effect_count", "effect_strength"]}
    if options.get("effect_profile") == GENTLE_PROFILE:
        policy.update(effect_profile=GENTLE_PROFILE, atmosphere_count=3, motion_count=2,
                      pool_version=gentle_groups()["version"])
    if options.get("music_source")=="local_index":policy["music_source"]="local_index"
    seed = object_hash({"version": AUTO_VERSION, "signature": plan["signature"],
        "seed": project.config["mix"].get("random_seed", 0), "policy": policy,
        "pools": object_hash({"effects": effects, "music": songs})})
    rng = random.Random(seed)
    start = sum(round(item["duration"]*1_000_000) for item in plan["clips"])
    duration = sum(round(item["seconds"]*1_000_000) for item in cfg["product_images"])
    if duration <= 0:
        raise PipelineError("图片尾部时长必须大于零。")
    selected = []
    if policy.get("effect_profile") == GENTLE_PROFILE:
        choices = rng.sample([item for item in effects if item["category"] == "氛围"], 3)
        choices += rng.sample([item for item in effects if item["category"] == "动感"], 2)
    else:
        choices = rng.sample(effects, 5)
    for item in choices:
        selected.append({**item, "params": effect_params(item["key"], float(options["effect_strength"]), policy.get("effect_profile", "legacy")),
            "start_us": start, "duration_us": duration})
    order = list(songs)
    rng.shuffle(order)
    # Prefer multiple songs over a single unusually long recording when possible.
    shorter = next((index for index,item in enumerate(order) if round(item["seconds"]*1_000_000) < duration), None)
    if shorter is not None:
        order.insert(0, order.pop(shorter))
    if sum(round(item["seconds"]*1_000_000) for item in order) < duration:
        raise PipelineError("音乐库中所有不同歌曲的总时长不足以覆盖图片尾部，请补充音乐索引。")
    playlist, cursor = [], start
    for item in order:
        length = min(round(item["seconds"]*1_000_000), start+duration-cursor)
        if length <= 0:
            break
        playlist.append({**item, "source_start_us": 0, "start_us": cursor, "duration_us": length})
        cursor += length
    cfg["packaging_plan"] = {"version": AUTO_VERSION, "seed": seed, "policy": policy,
        "group_basis": (gentle_groups() if policy.get("effect_profile") == GENTLE_PROFILE else effect_groups())["basis"], "start_us": start, "duration_us": duration,
        "effects": selected, "music": playlist}
    if options.get("music_source")=="local_index":
        from .local_music import verify_song
        for song in playlist:verify_song(song)


def frozen_packaging(plan: dict, cfg: dict) -> dict:
    """Reject stale or edited time ranges rather than letting effects cross the body."""
    value = copy.deepcopy(cfg.get("packaging_plan"))
    start = sum(round(item["duration"]*1_000_000) for item in plan["clips"])
    duration = sum(round(item["seconds"]*1_000_000) for item in cfg["product_images"])
    def fail():
        raise PipelineError("自动包装计划缺失或边界不正确，请选择所属批次，点击“应用到本批计划”后重试。")
    if not isinstance(value, dict) or value.get("version") != AUTO_VERSION or value.get("start_us") != start or value.get("duration_us") != duration:
        fail()
    effects = value.get("effects", [])
    indexed = {item["key"]: item for item in catalog("effects")}
    policy = value.get("policy", {})
    allowed = {item["key"] for item in effect_pool(policy)}
    if len(effects) != 5 or len({item.get("resource_id") for item in effects}) != 5:
        fail()
    for item in effects:
        meta = indexed.get(item.get("key"))
        if not meta or item["key"] not in allowed or item.get("resource_id") != meta["resource_id"] or item.get("start_us") != start or item.get("duration_us") != duration:
            fail()
        expected = effect_params(item["key"], float(policy.get("effect_strength", 25)), policy.get("effect_profile", "legacy"))
        if item.get("params") != expected:
            fail()
        if item.get("category") != next(meta["category"] for meta in effect_pool(policy) if meta["key"] == item["key"]):
            fail()
    if policy.get("effect_profile") == GENTLE_PROFILE:
        if sum(item.get("category") == "氛围" for item in effects) != 3 or sum(item.get("category") == "动感" for item in effects) != 2:
            fail()
    cursor, identities = start, set()
    playlist = value.get("music", [])
    if not playlist:
        fail()
    for index, item in enumerate(playlist):
        try:
            source_duration = round(float(item["seconds"])*1_000_000)
            length = item["duration_us"]
            identity = str(item["id"])
            local = identity.startswith("local-")
            if policy.get("music_source")=="local_index" and not local:fail()
            valid = ((identity.isdigit() or local) and identity not in identities and item["name"] and
                item.get("source_start_us") == 0 and item.get("start_us") == cursor and
                isinstance(length, int) and 0 < length <= source_duration and
                (index == len(playlist)-1 or length == source_duration) and
                (bool(str(item.get("path") or "").strip()) and bool(re.fullmatch(r"[a-f0-9]{64}",str(item.get("sha256") or ""))) if local else not item.get("path")))
        except (KeyError, ValueError, TypeError, OverflowError):
            fail()
        if not valid:
            fail()
        cursor += length
        identities.add(identity)
    if cursor != start+duration:
        fail()
    return value
