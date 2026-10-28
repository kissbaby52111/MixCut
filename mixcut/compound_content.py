"""Shared native compound envelope and recursive subdraft file publication."""
from __future__ import annotations
from pathlib import Path
import re
import time
import uuid
from .common import ROOT,read_json,write_json

ID_PATTERN=re.compile(r"(?:[0-9a-f]{32}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})",re.I)

def new_id():return str(uuid.uuid4()).upper()

def replace_strings(value,mapping):
    if isinstance(value,dict):return {k:replace_strings(v,mapping) for k,v in value.items()}
    if isinstance(value,list):return [replace_strings(v,mapping) for v in value]
    return mapping.get(value,value) if isinstance(value,str) else value

def isolated_child(content):
    identities=set()
    def collect(value):
        if isinstance(value,dict):
            identity=value.get("id")
            if isinstance(identity,str) and ID_PATTERN.fullmatch(identity):identities.add(identity)
            for item in value.values():collect(item)
        elif isinstance(value,list):
            for item in value:collect(item)
    collect(content)
    return replace_strings(content,{identity:new_id() for identity in sorted(identities)})

def compound_envelope(child: dict,name: str) -> dict:
    template=read_json(ROOT/"assets/jianying/compound_wrapper.json")
    mapping={"$segment":new_id(),"$video":new_id(),"$draft":new_id(),"$combination":new_id(),"$name":name}
    for group in template["materials"].values():
        for material in group:mapping[material["id"]]=new_id()
    value=replace_strings(template,mapping);duration=child["duration"]
    value["segment"].update(source_timerange={"start":0,"duration":duration},target_timerange={"start":0,"duration":duration},volume=1.,speed=1.)
    value["video"].update(duration=duration,width=child["canvas_config"]["width"],height=child["canvas_config"]["height"],material_name=name)
    value["draft"].update(draft=child,name=name)
    return value

def write_nested_drafts(content: dict,final_root: Path,staging_root: Path) -> None:
    """Publish sub-drafts under the same top-level subdraft folder.

    JianYing resolves a compound clip through subdraft/<draft id>/.  Writing a
    grandchild draft into subdraft/<child>/subdraft/<grandchild>/ keeps the JSON
    valid but the client cannot follow the second level, so every nested draft is
    published flat under the draft root instead.
    """
    for material in content.get("materials",{}).get("drafts",[]):
        child=material.get("draft")
        if not isinstance(child,dict):continue
        folder_id=child["id"]
        if not ID_PATTERN.fullmatch(folder_id):raise ValueError("子草稿标识无效")
        final=final_root/"subdraft"/folder_id;stage=staging_root/"subdraft"/folder_id
        material.update(draft_config_path=str(final/"sub_draft_config.json"),
            draft_cover_path=str(final/"draft_cover.jpg"),draft_file_path=str(final/"draft_content.json"))
        # Flat: the grandchild of a sub-draft is still written under the same root.
        write_nested_drafts(child,final_root,staging_root)
        for filename in ["draft_content.json","draft_info.json"]:write_json(stage/filename,child)
        canvas=child["canvas_config"];stamp=time.time()
        write_json(stage/"sub_draft_config.json",{"audio_path":"","cover_height":canvas["height"],"cover_width":canvas["width"],
            "cover_path":"draft_cover.jpg","create_time":int(stamp),"draft_json_file":"draft_content.json","id":folder_id,
            "import_time_ms":int(stamp*1000),"is_from_multi_timeline":False,"is_from_sub_draft":True,"material_color_tag":"",
            "name":child.get("name","内容"),"project_id":folder_id,"rough_cut_duration":child["duration"],"rough_cut_start":0,
            "source":"timeline","type":"video"})
