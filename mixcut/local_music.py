"""Anonymous offline music indexes, validation and frozen playlist replacement."""
from __future__ import annotations
from functools import lru_cache
import math
from pathlib import Path
import random
import re
import uuid
import copy
from .common import ROOT,PipelineError,read_json,file_hash,object_hash,write_json,now

DEFAULT_INDEX=ROOT/"assets/local_music/index.json"

def index_file(project,options):
    value=str(options.get("local_music_index") or "").strip()
    return project.asset(value) if value else DEFAULT_INDEX

def local_music_pool(project,options):
    index=index_file(project,options)
    try:data=read_json(index)
    except (OSError,ValueError) as error:raise PipelineError(f"本地曲库索引无法读取：{index}（{error}）") from None
    if not isinstance(data,list) or not data:raise PipelineError("本地曲库为空，请先点击‘导入剪映缓存’或‘导入音乐文件夹’："+str(index))
    result=[];seen=set()
    for item in data:
        if not isinstance(item,dict):raise PipelineError("曲库索引条目格式无效，请重新导入。")
        try:
            seconds=float(item["seconds"]);digest=str(item["sha256"]);path=Path(item["path"])
            if not math.isfinite(seconds) or not 0<seconds<=86400 or not re.fullmatch(r"[a-f0-9]{64}",digest):raise ValueError()
        except (KeyError,TypeError,ValueError):raise PipelineError("曲库索引时长或校验值无效，请重新导入。") from None
        if not path.is_absolute():
            candidates=[index.parent/path,ROOT/path,project.asset(str(path))]
            path=next((p for p in candidates if p.is_file()),candidates[0])
        if not path.is_file():raise PipelineError("本地曲库文件缺失，请恢复或重新导入："+str(path))
        if item.get("bytes") is not None and path.stat().st_size!=item["bytes"]:raise PipelineError("本地曲库文件大小已改变，请重新导入："+str(path))
        if digest in seen:continue
        seen.add(digest)
        result.append({"id":"local-"+digest[:16],"name":str(item.get("name") or f"曲库第 {len(result)+1:04} 首"),
            "seconds":seconds,"path":str(path.resolve()),"sha256":digest})
    return result

@lru_cache(maxsize=2048)
def _checked_hash(path,size,mtime):return file_hash(Path(path))

def verify_song(song):
    path=Path(song.get("path", ""));digest=str(song.get("sha256") or "")
    if not path.is_absolute() or not path.is_file() or not re.fullmatch(r"[a-f0-9]{64}",digest):
        raise PipelineError("计划中的本地配乐缺失或校验值无效，请恢复曲库或重新替换本批配乐。")
    stat=path.stat()
    if _checked_hash(str(path),stat.st_size,stat.st_mtime_ns)!=digest:
        raise PipelineError("计划中的本地配乐被修改，请恢复曲库文件："+str(path))

def playlist(songs,start,duration,seed):
    order=list(songs);random.Random(seed).shuffle(order)
    shorter=next((i for i,s in enumerate(order) if round(s["seconds"]*1e6)<duration),None)
    if shorter is not None:order.insert(0,order.pop(shorter))
    if sum(round(s["seconds"]*1e6) for s in order)<duration:
        raise PipelineError("本地曲库中不同歌曲的总时长不足以覆盖四图尾部，请导入更多音乐。")
    result=[];cursor=start
    for song in order:
        length=min(round(song["seconds"]*1e6),start+duration-cursor)
        if length<=0:break
        result.append({**song,"source_start_us":0,"start_us":cursor,"duration_us":length});cursor+=length
    return result

def replace_batch_music(project,*,batch_id=None,index=""):
    from .plan_batches import scoped_paths
    from .auto_packaging import frozen_packaging
    options={"local_music_index":index or str(DEFAULT_INDEX)};songs=local_music_pool(project,options)
    status=read_json(project.state/"render_status.json",{});changes=[]
    for path in scoped_paths(project,batch_id=batch_id):
        original=read_json(path)
        if status.get(original["id"],{}).get("status")=="complete":continue
        if original.get("mode")!="folder_mix" or original["render"].get("output_mode")!="jianying" or not original["render"].get("packaging_plan"):
            raise PipelineError("仅替换本批配乐需要已有的自动包装剪映计划。")
        value=frozen_packaging(original,original["render"]);plan=copy.deepcopy(original)
        seed=object_hash({"plan":plan["id"],"source":"local_index","songs":songs})
        value.update(music=playlist(songs,value["start_us"],value["duration_us"],seed),music_seed=seed)
        value["policy"]["music_source"]="local_index"
        for song in value["music"]:verify_song(song)
        plan["render"]["packaging_plan"]=value
        plan["render"]["jianying"].update(music_source="local_index",local_music_index=str(index_file(project,options)))
        frozen_packaging(plan,plan["render"]);changes.append((path,original,plan))
    backup=project.state/"backups"/("local_music_"+uuid.uuid4().hex[:12])
    if changes:
        write_json(backup/"render_status.json",status)
        for path,original,_ in changes:write_json(backup/path.name,original)
        for path,_,plan in changes:
            write_json(path,plan);status[plan["id"]]={"status":"pending","note":"已改成本地配乐，请重新生成本批草稿。"}
        write_json(project.state/"render_status.json",status)
    report={"count":len(changes),"changed_plans":[p["id"] for _,_,p in changes],"backup":str(backup) if changes else None,
        "index":str(index_file(project,options)),"clips_images_effects_corners_preserved":True,"time":now()}
    write_json(project.state/"last_local_music_replacement.json",report)
    return report
