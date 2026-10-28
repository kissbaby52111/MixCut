"""Lift the four frozen corner images onto the top of one flat timeline."""
from __future__ import annotations
import json
from .corner_overlay import geometry,prepared_image


def merge_corners(draft,project,content,value,cfg):
    """Add four full-length corner tracks above everything else in the draft.

    The five tail effects are attached to the product-image segments themselves,
    so they only cover those segments; the corner images live on their own video
    tracks on top of the composition and stay clean.  Keeping one flat timeline
    (instead of a compound wrapper) is what lets each clip become a single
    exportable compound segment inside the batch draft.
    """
    width,height=int(cfg["width"]),int(cfg["height"])
    script=draft.ScriptFile(width,height,fps=int(cfg["fps"]),maintrack_adsorb=True)
    placements=[]
    for image in value["images"]:
        g=geometry(image,width,height,value["policy"]);name="四角产品图_"+image["position"]
        script.append_track(draft.TrackSpec(draft.TrackType.video,name))
        script.add_segment(draft.VideoSegment(str(prepared_image(project,image)),draft.trange(0,content["duration"]),volume=0,
            clip_settings=draft.ClipSettings(scale_x=g["scale"],scale_y=g["scale"],transform_x=g["transform_x"],transform_y=g["transform_y"])),name)
        placements.append({**image,"geometry":g})
    overlay=json.loads(script.dumps())
    for group,items in overlay.get("materials",{}).items():
        content["materials"].setdefault(group,[]).extend(items)
    for track in overlay.get("tracks",[]):content["tracks"].append(track)
    # Corner tracks are appended last, so they render above the content and effects.
    for index,track in enumerate(content["tracks"]):
        for segment in track.get("segments",[]):segment.update(render_index=index,track_render_index=0)
    return content,{"enabled":True,"scope":"entire_output","edge_margin_pixels":0,"images":placements,
        "width_percent":value["policy"]["width_percent"],"outermost_tracks":True,
        "effects_isolated_by_segment_scope":True,"client_load_verified":False}
