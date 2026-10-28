"""Run a bounded visual classification trial on approved videos.

Credentials are loaded locally; results stay separate from production clips.
No speech recognition or auto-approval is implied by this visual-only trial.
"""
from __future__ import annotations
import argparse
import copy
from datetime import datetime
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mixcut.analyze import PROMPT_VERSION, classify, export_clip
from mixcut.cli import project_lock
from mixcut.common import Project, ROLES, PipelineError, file_hash, load_env, object_hash, read_json, video_duration, write_json


def sample_ranges(duration: float, seconds: float) -> list[tuple[float, float]]:
    length = min(seconds, duration)
    return list(dict.fromkeys((round(start, 3), round(min(duration, start + length), 3))
        for start in [0.0, max(0.0, (duration-length)/2), max(0.0, duration-length)]))


def run(config_path: Path, max_videos: int = 2, seconds: float = 8) -> Path:
    load_env()
    original = Project(config_path)
    cfg = original.config["analysis"]
    if not cfg.get("base_url") or not cfg.get("model"):
        raise PipelineError("请先保存视觉模型地址和名称。")
    if not os.getenv(cfg.get("api_key_env", "MIXCUT_API_KEY"), "").strip():
        raise PipelineError("尚未保存视觉服务 API Key；本轮没有调用模型。")
    sources = original.approved_videos()[:max_videos]
    if not sources:
        raise PipelineError("请先在素材审核页保留需要测试的原片。")
    run_root = original.path / "08_AI实测" / (datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + object_hash(cfg)[:6])
    config = copy.deepcopy(original.config)
    config["workspace"] = "work"
    # Relative packaging paths should keep their meaning, although this trial never renders.
    for name in ["product_image", "music", "effect_video"]:
        if config["render"].get(name):
            config["render"][name] = str(original.asset(config["render"][name]))
    write_json(run_root / "config.json", config)
    project = Project(run_root / "config.json")
    report = {"model": cfg["model"], "base_url": cfg["base_url"], "product": project.keyword,
        "mode": "visual_only", "prompt_version": PROMPT_VERSION,
        "note": "人工抽查试验；没有语音转写，分类置信度不代表准确率，导出的片段需人工确认。",
        "cases": [], "errors": []}
    with project_lock(project):
        for source in sources:
            duration = video_duration(source)
            digest = file_hash(source)
            for index, (start, end) in enumerate(sample_ranges(duration, seconds), 1):
                case = {"source": str(source), "source_sha256": digest, "start": start, "end": end,
                    "position": {1:"开头取样", 2:"中段取样", 3:"末段取样"}[index]}
                print(f"测试 {source.name} · {start:.2f}–{end:.2f} 秒", flush=True)
                try:
                    result = classify(project, source, start, end, "")
                    result.update(speech_available=False, boundary_review_required=True)
                    case["result"] = result
                    if result["role"] != "reject":
                        case["clip"] = export_clip(project, source, start, end, result["role"], result=result)
                    print(f"  {ROLES.get(result['role'], '不采用')} · 置信度 {result['confidence']:.2f} · 商品相关 {result['product_match']}", flush=True)
                    report["cases"].append(case)
                except PipelineError as error:
                    report["errors"].append({**case, "error": str(error)})
                    print(f"  未完成：{error}", flush=True)
                    # An auth or transport failure should not trigger the rest of the paid batch.
                    write_json(run_root / "report.json", report)
                    raise
                write_json(run_root / "report.json", report)
    lines = ["# 双头染发膏 · 视觉分类实测", "", f"模型：`{cfg['model']}`。{report['note']}", "",
        "取样位置仅用于覆盖原片不同部分，不代表期待模型输出相同角色。", "",
        "| 原片 | 时间范围 / 秒 | 模型分类 | 置信度 | 商品相关 | 说明 |", "| --- | --- | --- | --- | --- | --- |"]
    def cell(value):
        return str(value).replace("|", "／").replace("\n", " ")
    for case in report["cases"]:
        result = case["result"]
        lines.append("| " + " | ".join(cell(value) for value in [Path(case["source"]).name,
            f"{case['start']:.2f}–{case['end']:.2f}", ROLES.get(result["role"], "不采用"),
            f"{result['confidence']:.2f}", "是" if result["product_match"] else "否", result.get("reason", "")]) + " |")
    lines += ["", "导出的片段位于下方工作目录的 `03_分类片段/待确认`；未改动生产项目分类库。", ""]
    (run_root / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"报告：{run_root / 'report.md'}", flush=True)
    return run_root


def main():
    parser = argparse.ArgumentParser(description="对最多两条已通过原片进行开头、中段、末段视觉分类抽样。")
    parser.add_argument("--config", type=Path, default=ROOT / "config.json")
    parser.add_argument("--max-videos", type=int, choices=range(1, 6), default=2)
    parser.add_argument("--seconds", type=float, default=8)
    args = parser.parse_args()
    if not 1 <= args.seconds <= 30:
        parser.error("--seconds 需要在 1–30 秒之间。")
    try:
        run(args.config, args.max_videos, args.seconds)
    except (PipelineError, OSError, ValueError) as error:
        print(f"实测未完成：{error}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
