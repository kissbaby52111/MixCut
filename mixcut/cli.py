from __future__ import annotations
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sys
from .common import ROOT, Project, PipelineError, ROLES, VIDEO_EXTS, load_env, read_json, write_json, runtime_tool, run_command

@contextmanager
def project_lock(project: Project):
    path = project.state / "pipeline.lock"
    with path.open("a+b") as lock:
        lock.seek(0, 2)
        if lock.tell() == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise PipelineError("同一商品已有流水线任务在运行，请等待或停止原任务。") from None
        try:
            yield
        finally:
            lock.seek(0)
            if os.name == "nt":
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock, fcntl.LOCK_UN)

def summary(project: Project) -> dict:
    return {"product": project.keyword, "workspace": str(project.path), "pending_review": len([p for p in project.inbox.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXTS and ".partial." not in p.name]), "approved_videos": len(project.approved_videos()), "clips": {label: len([p for p in (project.clips / label).glob("*.mp4") if ".partial." not in p.name]) for label in [*ROLES.values(), "待确认"]}, "plans": len(list(project.plans.glob("*.json"))), "outputs": len([p for p in project.outputs.glob("*.mp4") if ".partial." not in p.name]), "ai_configured": bool(project.config["analysis"].get("base_url") and project.config["analysis"].get("model"))}

def doctor(project: Project) -> dict:
    from .folder_mix import is_folder_mode, folder_status
    checks = {}
    for tool in (["ffmpeg", "ffprobe"] if is_folder_mode(project) else ["ffmpeg", "ffprobe", "socai"]):
        try:
            exe = runtime_tool(tool)
            response = run_command([exe, "--version" if tool == "socai" else "-version"], timeout=20)
            checks[tool] = {"ok": True, "version": response.stdout.splitlines()[0], "path": exe}
        except Exception as exc:
            checks[tool] = {"ok": False, "error": str(exc)}
    checks["ai"] = {"required": not is_folder_mode(project), "configured": bool(project.config["analysis"].get("base_url") and project.config["analysis"].get("model")), "note": "文件夹拼接不调用 AI。" if is_folder_mode(project) else "尚未配置时不会执行真实 AI 分类；可手工标注或运行合成素材演示。"}
    if is_folder_mode(project):
        checks["folders"] = folder_status(project)
    for key in ["product_image", "music"]:
        value = project.config["render"].get(key)
        checks[key] = {"configured": bool(value), "exists": bool(value and project.asset(value).is_file())}
    write_json(project.state / "doctor.json", checks)
    return checks

def show(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))

def menu(project: Project):
    from .folder_mix import is_folder_mode
    if is_folder_mode(project):
        from .gui import launch
        launch(project.config_path)
        return
    print(f"\nMixCut 本地流水线 | 商品：{project.keyword}")
    print("1 关键词采集    2 打开待审核目录    3 打开审核通过目录")
    print("4 AI 拆分分类   5 打开分类片段目录  6 生成组合计划")
    print("7 渲染/续跑     8 查看状态          9 环境检查")
    print("D 合成测试素材出片（非真实采集）   0 退出")
    while True:
        choice = input("\n请选择：").strip().lower()
        if choice == "0":
            return
        try:
            if choice in {"2", "3", "5"}:
                folder = {"2": project.inbox, "3": project.approved, "5": project.clips}[choice]
                os.startfile(str(folder))
            elif choice == "8":
                show(summary(project))
            elif choice == "9":
                show(doctor(project))
            elif choice == "d":
                from .demo import make_demo
                print("将生成带明显测试标识的合成素材。")
                show(make_demo())
            elif choice in {"1", "4", "6", "7"}:
                with project_lock(project):
                    if choice == "1":
                        from .collect import collect
                        show(collect(project))
                    elif choice == "4":
                        from .analyze import analyze
                        show(analyze(project))
                    elif choice == "6":
                        from .planning import make_plans
                        show(make_plans(project))
                    elif choice == "7":
                        from .render import render_pending
                        show(render_pending(project))
            else:
                print("请输入菜单中的编号。")
        except (PipelineError, OSError, KeyboardInterrupt) as exc:
            print(f"\n任务未完成：{exc}\n已完成的文件和计划会保留。")

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MixCut：商品视频采集、人工审核、AI 分类、随机组合与长图片段包装。")
    parser.add_argument("--config", default=str(ROOT / "config.json"))
    sub = parser.add_subparsers(dest="command")
    for command in ["init", "status", "doctor", "analyze", "menu", "gui"]:
        sub.add_parser(command)
    render = sub.add_parser("render",help="续跑当前批次（文件夹模式）；历史需显式选择")
    render.add_argument("plan_ids",nargs="*")
    render_scope = render.add_mutually_exclusive_group()
    render_scope.add_argument("--batch",help="指定批次编号，current 为最新批次")
    render_scope.add_argument("--all",dest="all_plans",action="store_true",help="显式处理全部历史计划")
    collect = sub.add_parser("collect")
    collect.add_argument("--limit", type=int)
    imp = sub.add_parser("import")
    imp.add_argument("files", nargs="+")
    approve = sub.add_parser("approve")
    approve.add_argument("files", nargs="*")
    approve.add_argument("--all", action="store_true", help="仅在已审查全部文件后使用")
    clip = sub.add_parser("clip")
    clip.add_argument("source")
    clip.add_argument("--start", type=float, required=True)
    clip.add_argument("--end", type=float, required=True)
    clip.add_argument("--role", choices=list(ROLES), required=True)
    plan = sub.add_parser("plan")
    plan.add_argument("--count", type=int)
    demo = sub.add_parser("demo")
    demo.add_argument("--tail-seconds", type=float, default=300)
    demo.add_argument("--count", type=int, default=1)
    demo.add_argument("--width", type=int, default=720)
    demo.add_argument("--height", type=int, default=1280)
    demo.add_argument("--make-only", action="store_true")
    draft = sub.add_parser("draft")
    draft.add_argument("plan_id")
    draft.add_argument("--output", default="")
    batch = sub.add_parser("batch-draft", help="将剪映计划封装为同一时间线上的独立复合片段")
    batch.add_argument("plan_ids", nargs="*", help="留空仅使用当前批次，或显式给出计划编号")
    batch_scope = batch.add_mutually_exclusive_group()
    batch_scope.add_argument("--batch",help="指定批次编号，current 为最新批次")
    batch_scope.add_argument("--all",dest="all_plans",action="store_true",help="显式合并全部历史剪映计划")
    batch.add_argument("--output", default="")
    repair = sub.add_parser("repair-music", help="替换客户端明确报失效的音乐，保留其它选材和特效")
    repair.add_argument("unavailable_ids", nargs="+")
    inspect = sub.add_parser("inspect-draft", help="检查单个草稿目录的嵌套层级、轨道及缺失媒体")
    inspect.add_argument("draft_path")
    tidy = sub.add_parser("tidy-drafts", help="把本批以外的 MixCut 草稿移到归档子目录，只保留当前批次")
    tidy.add_argument("--keep", action="append", default=[], help="额外保留的草稿文件夹名，可重复")
    tidy.add_argument("--keep-singles", action="store_true", help="同时保留本批的单条草稿")
    tidy.add_argument("--output", default="", help="草稿根目录，默认使用配置里的 draft_root")
    harvest = sub.add_parser("harvest-music", help="把剪映缓存的云端音乐复制成本地曲库")
    harvest.add_argument("--source", default="", help="剪映音乐缓存目录，默认 %LOCALAPPDATA%\\JianyingPro\\User Data\\Cache\\music")
    harvest.add_argument("--target", default="", help="本地曲库目录，默认 assets/local_music")
    harvest.add_argument("--min-seconds", type=float, default=120, help="最短时长，过短的试听片段会被跳过")
    harvest.add_argument("--limit", type=int, default=0, help="最多抓取多少首，0 表示不限制")
    localize=sub.add_parser("localize-music",help="仅将所选批次的配乐换成本地曲库，保留视频、图片与特效")
    localize.add_argument("--batch",default="current")
    localize.add_argument("--index",default="")
    return parser

def main(argv=None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    load_env()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        project = Project(args.config)
        from .folder_mix import is_folder_mode, create_default_folders
        if is_folder_mode(project):
            create_default_folders(project)
        command = args.command or "menu"
        if is_folder_mode(project) and command in {"collect", "analyze", "clip", "approve", "import"}:
            raise PipelineError("当前为文件夹拼接模式，请重新打开 start.bat，在各段落文件夹放好素材后出片。采集和 AI 拆分已停用。")
        if command == "gui":
            from .gui import launch
            launch(project.config_path)
        elif command == "demo":
            from .demo import make_demo
            show(make_demo(tail_seconds=args.tail_seconds, count=args.count, width=args.width, height=args.height, render=not args.make_only))
        elif command == "menu":
            menu(project)
        elif command in {"status", "init"}:
            show(summary(project))
        elif command == "doctor":
            show(doctor(project))
        elif command == "inspect-draft":
            from .draft import inspect_draft
            show(inspect_draft(args.draft_path))
        else:
            with project_lock(project):
                if command == "collect":
                    from .collect import collect
                    show(collect(project, limit=args.limit))
                elif command == "import":
                    from .collect import save_local_video
                    show([save_local_video(project, Path(p), {"platform": "local"}) for p in args.files])
                elif command == "approve":
                    from .collect import approve
                    show({"approved": approve(project, args.files, all_files=args.all)})
                elif command == "analyze":
                    from .analyze import analyze
                    show(analyze(project))
                elif command == "clip":
                    from .analyze import export_clip
                    show(export_clip(project, Path(args.source), args.start, args.end, args.role, manual=True))
                elif command == "plan":
                    from .planning import make_plans
                    show(make_plans(project, args.count))
                elif command == "render":
                    from .render import render_pending
                    show(render_pending(project,args.plan_ids or None,batch_id=args.batch,all_plans=args.all_plans))
                elif command == "draft":
                    from .draft import export_draft
                    show(export_draft(project, args.plan_id, args.output))
                elif command == "batch-draft":
                    from .compound_draft import export_batch_draft
                    show(export_batch_draft(project, args.plan_ids or None, args.output,batch_id=args.batch,all_plans=args.all_plans))
                elif command == "repair-music":
                    from .music_repair import repair_music
                    show(repair_music(project, args.unavailable_ids))
                elif command == "tidy-drafts":
                    from .compound_draft import archive_old_drafts
                    show(archive_old_drafts(project, keep=args.keep, keep_singles=args.keep_singles, output=args.output))
                elif command == "harvest-music":
                    from .harvest import harvest_music
                    show(harvest_music(source=args.source, target=args.target,
                        min_seconds=args.min_seconds, limit=args.limit))
                elif command == "localize-music":
                    from .local_music import replace_batch_music
                    show(replace_batch_music(project,batch_id=args.batch,index=args.index))
    except (PipelineError, OSError, ValueError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        raise SystemExit(1)
    except KeyboardInterrupt:
        print("已停止；已完成的素材和输出会保留，重跑对应阶段即可。", file=sys.stderr)
        raise SystemExit(130)
