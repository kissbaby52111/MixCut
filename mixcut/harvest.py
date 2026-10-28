"""Import plain local audio into a managed, resumable offline music library."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
import math
import os
from pathlib import Path
import subprocess
import uuid
from .common import ROOT,PipelineError,now,runtime_tool,media_info,run_command,file_hash,read_json,write_json

DEFAULT_SOURCE=Path(os.environ.get("LOCALAPPDATA", "") or ".")/"JianyingPro/User Data/Cache/music"
DEFAULT_TARGET=ROOT/"assets/local_music"
AUDIO_SUFFIXES={".mp3",".m4a",".aac",".wav",".flac",".ogg",".opus",".wma",".aiff"}
DEFAULT_MIN_SECONDS=120.

def media_summary(path: Path) -> dict:
    info=media_info(path)
    audio=next((s for s in info.get("streams",[]) if s.get("codec_type")=="audio"),None)
    if not audio:raise PipelineError("没有音频流")
    seconds=float(audio.get("duration") or info.get("format",{}).get("duration") or 0)
    if not math.isfinite(seconds) or seconds<=0:raise PipelineError("音频时长无效")
    return {"codec":audio.get("codec_name"),"seconds":seconds,"sample_rate":audio.get("sample_rate"),"channels":audio.get("channels")}

def harvest_music(*,source="",target="",min_seconds=DEFAULT_MIN_SECONDS,limit=0,copy=True) -> dict:
    """Managed copies survive cache removal. AAC is remuxed to M4A; every new
    file is fully decoded. Per-file checkpoints survive cancelled imports."""
    source_dir=Path(source or DEFAULT_SOURCE).resolve();target_dir=Path(target or DEFAULT_TARGET).resolve()
    if not math.isfinite(float(min_seconds)) or not 0<=min_seconds<=86400 or not isinstance(limit,int) or limit<0:
        raise PipelineError("最短时长应为 0–86400 秒，导入数量上限应为非负整数。")
    if not source_dir.is_dir():raise PipelineError("找不到音乐来源目录："+str(source_dir))
    if source_dir==target_dir or source_dir.is_relative_to(target_dir):
        raise PipelineError("曲库目标目录不能与来源相同，也不能包含来源目录。")
    paths=sorted(p for p in source_dir.iterdir() if p.is_file() and p.suffix.lower() in AUDIO_SUFFIXES and ".partial." not in p.name)
    if not paths:raise PipelineError("来源目录没有支持的音频文件；原曲库保持不变。")
    target_dir.mkdir(parents=True,exist_ok=True);audio_dir=target_dir/"audio";audio_dir.mkdir(exist_ok=True)
    journal_dir=target_dir/".imports";journal_dir.mkdir(exist_ok=True)
    old=read_json(target_dir/"index.json",[])
    if not isinstance(old,list):raise PipelineError("已有曲库索引格式错误；已保留原文件。")
    entries={i["sha256"]:i for i in old if isinstance(i,dict) and i.get("sha256")}
    known={i.get("source_sha256"):i for i in entries.values() if i.get("source_sha256")}
    for path in journal_dir.glob("*.json"):
        item=read_json(path,{})
        if item.get("source_sha256"):known[item["source_sha256"]]=item
    ffmpeg=runtime_tool("ffmpeg")
    def absolute(item):
        path=Path(item["path"])
        return path if path.is_absolute() else target_dir/path
    def process(path):
        temporary=None
        try:
            digest=file_hash(path);previous=known.get(digest)
            if previous and absolute(previous).is_file() and file_hash(absolute(previous))==previous["sha256"]:
                return "reused",previous
            info=media_summary(path)
            if info["seconds"]<min_seconds:return "short",None
            suffix=".m4a" if info["codec"] in {"aac","alac"} else ".mp3" if info["codec"]=="mp3" else ".m4a"
            final=audio_dir/(digest+suffix);temporary=audio_dir/(digest+".partial_"+uuid.uuid4().hex[:8]+suffix)
            codec=["-c:a","copy"] if info["codec"] in {"aac","alac","mp3"} else ["-c:a","aac","-b:a","192k"]
            run_command([ffmpeg,"-v","error","-nostdin","-y","-i",str(path),"-map","0:a:0","-vn","-map_metadata","-1",*codec,str(temporary)],timeout=180)
            run_command([ffmpeg,"-v","error","-xerror","-nostdin","-i",str(temporary),"-map","0:a:0","-vn","-f","null","-"],timeout=180)
            verified=media_summary(temporary)
            if verified["seconds"]<min_seconds:return "short",None
            normalized_hash=file_hash(temporary);temporary.replace(final)
            item={"id":"local-"+normalized_hash[:16],"path":str(final.relative_to(target_dir)),
                "sha256":normalized_hash,"source_sha256":digest,"source_file":path.name,
                "seconds":math.floor(verified["seconds"]*1000)/1000,"codec":verified["codec"],
                "sample_rate":verified["sample_rate"],"channels":verified["channels"],"bytes":final.stat().st_size,
                "fully_decoded":True}
            return "new",item
        except (PipelineError,OSError,ValueError,subprocess.SubprocessError) as error:
            return "broken",{"source_file":path.name,"error":str(error)[-350:]}
        finally:
            if temporary and temporary.exists():temporary.unlink()
    counts={"new":0,"reused":0,"short":0,"broken":0,"duplicate":0};failures=[];seen=set()
    next_number=max((i.get("number",0) for i in [*entries.values(),*known.values()]),default=0)+1
    candidates=paths[:limit] if limit else paths
    print(f"开始导入 {len(candidates)} 个音频；完成封装校正和全曲解码后才加入曲库。",flush=True)
    with ThreadPoolExecutor(max_workers=4) as workers:
        for n,(status,item) in enumerate(workers.map(process,candidates),1):
            counts[status]+=1
            if status in {"new","reused"}:
                digest=item["sha256"]
                if digest in seen:counts["duplicate"]+=1
                seen.add(digest)
                if digest in entries:item=entries[digest]
                elif not item.get("number"):
                    item={**item,"number":next_number,"name":f"曲库第 {next_number:04} 首"};next_number+=1
                entries[digest]=item
                write_json(journal_dir/(item["source_sha256"]+".json"),item)
            elif status=="broken":failures.append(item)
            if n%25==0 or n==len(candidates):
                print(f"已检查 {n}/{len(candidates)}：新增 {counts['new']}，复用 {counts['reused']}，过短 {counts['short']}，损坏 {counts['broken']}。",flush=True)
    if not entries:raise PipelineError("没有导入可用音乐，原索引保持不变。请查看来源文件或降低最短时长。")
    data=sorted(entries.values(),key=lambda i:(i.get("number",0),i["id"]))
    write_json(target_dir/"index.json",data)
    report={"source":str(source_dir),"target":str(target_dir),"index":str(target_dir/"index.json"),
        "harvested":len(data),"total_seconds":round(sum(i["seconds"] for i in data),3),"counts":counts,
        "errors":failures,"min_seconds":min_seconds,"time":now(),"fully_decoded":True}
    report["total_hours"]=round(report["total_seconds"]/3600,2)
    write_json(target_dir/"library.json",report)
    return report
