"""Freeze four corner images and calculate flush, aspect-preserving placement."""
from __future__ import annotations
import copy
import math
from pathlib import Path
import random
from .common import PipelineError, file_hash, object_hash

VERSION = "flush-corners-v1"
POSITIONS = ["左上", "右上", "左下", "右下"]


def settings(cfg: dict) -> dict:
    return {"enabled":False,"folder":"","width_percent":18.,"max_height_percent":15.,
            **cfg.get("corner_overlay",{})}


def validate_options(options: dict) -> dict:
    value = dict(options)
    if not isinstance(value.get("enabled"),bool):
        raise PipelineError("四角产品图开关必须为布尔值。")
    for key,label,low,high in [("width_percent","四角图片宽度",5,30),("max_height_percent","四角图片最大高度",5,25)]:
        try:number=float(value[key])
        except (KeyError,ValueError,TypeError):number=math.nan
        if not math.isfinite(number) or not low<=number<=high:
            raise PipelineError(f"{label}应为画面的 {low}–{high}%。")
        value[key]=number
    value["folder"]=str(value.get("folder","")).strip()
    return value


def image_pool(project,options: dict) -> list[dict]:
    from .folder_mix import files_in, IMAGE_EXTS, settings as folder_settings
    from PIL import Image,ImageOps
    folder=options["folder"] or folder_settings(project)["image_folder"]
    if not folder or not project.asset(folder).is_dir():
        raise PipelineError("请选择存在的四角产品图文件夹，或在素材页配置产品图文件夹。")
    result=[];seen=set()
    for path in files_in(project,folder,IMAGE_EXTS):
        digest=file_hash(path)
        if digest in seen:continue
        try:
            with Image.open(path) as image:
                image=ImageOps.exif_transpose(image);image.load();width,height=image.size
        except (OSError,ValueError) as error:
            raise PipelineError("四角产品图无法读取："+str(path)) from error
        result.append({"path":str(path.resolve()),"sha256":digest,"width":width,"height":height})
        seen.add(digest)
    if len(result)<4:
        raise PipelineError(f"四角产品图至少需要 4 张内容不同的图片，目前只有 {len(result)} 张；同图改名不算不同图片。")
    return result


def attach_corner_overlay(project,plan: dict,previous: dict | None = None) -> None:
    cfg=plan["render"];options=validate_options(settings(cfg))
    if not options["enabled"]:
        cfg.pop("corner_overlay_plan",None);return
    # Size-only changes keep the four images; changing the source folder draws anew.
    old=previous or {}
    from .folder_mix import settings as folder_settings
    source_folder=str(project.asset(options["folder"] or folder_settings(project)["image_folder"]))
    if settings(old)["enabled"] and old.get("corner_overlay_plan",{}).get("source_folder")==source_folder:
        images=copy.deepcopy(frozen_corners(project,old)["images"])
    else:
        pool=image_pool(project,options)
        seed=object_hash({"version":VERSION,"signature":plan["signature"],
            "seed":project.config["mix"].get("random_seed",0),"images":pool})
        images=copy.deepcopy(random.Random(seed).sample(pool,4))
    for position,image in zip(POSITIONS,images):image["position"]=position
    cfg["corner_overlay_plan"]={"version":VERSION,"policy":options,"source_folder":source_folder,"images":images}


def frozen_corners(project,cfg: dict) -> dict | None:
    options=validate_options(settings(cfg))
    if not options["enabled"]:return None
    value=copy.deepcopy(cfg.get("corner_overlay_plan"))
    if not isinstance(value,dict) or value.get("version")!=VERSION or value.get("policy")!=options:
        raise PipelineError("四角选图计划缺失或设置已更改，请选择所属批次并点击‘应用到本批计划’。")
    images=value.get("images",[])
    if len(images)!=4 or len({i.get("sha256") for i in images})!=4 or [i.get("position") for i in images]!=POSITIONS:
        raise PipelineError("四角选图计划需要四张不同图片，顺序为左上、右上、左下、右下。")
    for image in images:
        path=project.asset(image["path"])
        if not path.is_file() or file_hash(path)!=image["sha256"]:
            raise PipelineError("四角图片被移动或修改，请恢复图片或重新应用本批设置："+str(path))
        if not all(isinstance(image.get(k),int) and image[k]>0 for k in ["width","height"]):
            raise PipelineError("四角图片尺寸无效，请重新应用本批设置。")
    return value


def geometry(image: dict,width: int,height: int,options: dict) -> dict:
    factor=min(width*options["width_percent"]/100/image["width"],
               height*options["max_height_percent"]/100/image["height"])
    w=image["width"]*factor;h=image["height"]*factor
    right=image["position"] in {"右上","右下"};top=image["position"] in {"左上","右上"}
    fit=min(width/image["width"],height/image["height"])
    return {"width":w,"height":h,"x":width-w if right else 0.,"y":0. if top else height-h,
        "scale":factor/fit,"transform_x":(1-w/width)*(1 if right else -1),
        "transform_y":(1-h/height)*(1 if top else -1)}


def prepared_image(project,image: dict) -> Path:
    """Normalize EXIF orientation once; both renderers use the same geometry."""
    from PIL import Image,ImageOps
    target=project.state/"cache"/("corner_"+image["sha256"]+".png")
    if not target.is_file():
        target.parent.mkdir(parents=True,exist_ok=True)
        with Image.open(project.asset(image["path"])) as source:
            normalized=ImageOps.exif_transpose(source).convert("RGBA")
            temporary=target.with_suffix(".partial.png");normalized.save(temporary);temporary.replace(target)
    return target


def render_overlay(project,source: Path,target: Path,value: dict,cfg: dict) -> None:
    from .common import run_command,runtime_tool
    from .render import encoding
    width,height,fps=int(cfg["width"]),int(cfg["height"]),int(cfg["fps"])
    cmd=[runtime_tool("ffmpeg"),"-v","error","-nostdin","-y","-i",str(source)]
    filters=[];last="0:v"
    for index,image in enumerate(value["images"],1):
        cmd += ["-loop","1","-framerate",str(fps),"-i",str(prepared_image(project,image))]
        g=geometry(image,width,height,value["policy"])
        w=max(2,2*round(g["width"]/2));h=max(2,2*round(g["height"]/2))
        x=width-w if image["position"].startswith("右") else 0
        y=0 if image["position"].endswith("上") else height-h
        filters += [f"[{index}:v]scale={w}:{h},setsar=1,format=rgba[c{index}]",
                    f"[{last}][c{index}]overlay={x}:{y}:shortest=1:format=auto[v{index}]"]
        last=f"v{index}"
    cmd += ["-filter_complex_threads","1","-filter_complex",";".join(filters),"-map",f"[{last}]","-map","0:a:0?",*encoding(cfg),"-c:a","copy",str(target)]
    from .common import video_duration
    run_command(cmd,timeout=max(180,int(video_duration(source)*5)))
