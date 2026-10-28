"""Small batch manifests keep new work separate from historical plans."""
from __future__ import annotations

from pathlib import Path
import re
import uuid
from .common import PipelineError, now, read_json, write_json


def new_batch_id() -> str:
    return uuid.uuid4().hex[:16]


def checked_ids(identities: list[str]) -> list[str]:
    if not identities or any(not isinstance(i,str) or not re.fullmatch(r"[a-f0-9]{16}",i) for i in identities):
        raise PipelineError("批次需要有效的 16 位计划编号。")
    if len(set(identities)) != len(identities):
        raise PipelineError("同一批次不能重复添加同一计划。")
    return list(identities)


def record_batch(project, identities: list[str], *, batch_id: str = "", created_at: str = "", source: str = "generation", make_current: bool = True) -> dict:
    identities = checked_ids(identities)
    batch_id = batch_id or new_batch_id()
    if not re.fullmatch(r"[a-f0-9]{16}", batch_id):
        raise PipelineError("批次编号不正确。")
    for identity in identities:
        if not (project.plans / f"{identity}.json").is_file():
            raise PipelineError("批次中的计划不存在："+identity)
    record = {"batch_id":batch_id,"created_at":created_at or now(),"plan_ids":identities,
              "count":len(identities),"source":source}
    path = project.state / "plan_batches" / f"{batch_id}.json"
    previous = read_json(path)
    if previous and previous != record:
        raise PipelineError("批次编号已存在，已保留原批次。")
    write_json(path, record)
    if make_current:
        write_json(project.state / "current_plan_batch.json", {"batch_id":batch_id})
    return record


def get_batch(project, batch_id: str | None = None, *, required: bool = True) -> dict | None:
    if batch_id in {None,"current"}:
        batch_id = read_json(project.state / "current_plan_batch.json", {}).get("batch_id")
    if not batch_id:
        if not required:
            return None
        raise PipelineError("尚无当前批次，请先生成新计划；旧计划可在历史列表选中后单独生成，命令行合并全部历史需显式使用 --all。")
    if not isinstance(batch_id,str) or not re.fullmatch(r"[a-f0-9]{16}",batch_id):
        raise PipelineError("批次编号不正确。")
    value = read_json(project.state / "plan_batches" / f"{batch_id}.json")
    if not value or value.get("batch_id") != batch_id:
        raise PipelineError("批次记录不存在，请重新选择批次。")
    checked_ids(value.get("plan_ids",[]))
    return value


def list_batches(project) -> list[dict]:
    records=[]
    for path in (project.state / "plan_batches").glob("*.json"):
        records.append(get_batch(project,path.stem))
    return sorted(records,key=lambda b:(b["created_at"],b["batch_id"]),reverse=True)


def scoped_paths(project, plan_ids: list[str] | None = None, *, batch_id: str | None = None, all_plans: bool = False) -> list[Path]:
    if sum([plan_ids is not None,batch_id is not None,all_plans]) > 1:
        raise PipelineError("计划编号、批次和全部历史只能选择一种范围。")
    if plan_ids is not None:
        identities = checked_ids(plan_ids)
    elif all_plans or (project.config.get("workflow","ai_pipeline") != "folder_mix" and batch_id is None):
        return sorted(project.plans.glob("*.json"))
    else:
        identities = get_batch(project,batch_id)["plan_ids"]
    paths=[project.plans / f"{identity}.json" for identity in identities]
    for path in paths:
        if not path.is_file():
            raise PipelineError("批次中的计划不存在："+path.stem)
    return paths


def batch_compound(project, batch_id: str | None = None) -> dict | None:
    batch=get_batch(project,batch_id,required=False)
    if not batch:
        return None
    result=read_json(project.state / "batch_compound_drafts" / (batch["batch_id"]+".json"),{})
    # Read-only compatibility for a previously generated draft with exactly this scope.
    if not result:
        result=read_json(project.state / "last_compound_draft.json",{})
    actual=[c["plan_id"] for c in result.get("clips",[])]
    return result if actual==batch["plan_ids"] else None
