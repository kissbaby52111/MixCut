"""Small desktop interface for already prepared video and image folders."""
from __future__ import annotations
import queue
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog
import uuid
from .common import ROOT, VIDEO_EXTS, PipelineError, read_json
from .folder_mix import settings, files_in, IMAGE_EXTS
from .folder_service import FolderService
from .gui import MixCutApp, ScrollPage, BG, MUTED, SCOPE_LABELS, duration_text
from .ui_service import JobRunner
from .jianying_assets import native_settings, catalog
from .asset_picker import AssetPicker
from .auto_packaging import effect_pool, music_pool, GENTLE_PROFILE, gentle_groups
from .plan_batches import list_batches, get_batch, batch_compound
from .corner_overlay import settings as corner_settings
from .local_music import DEFAULT_INDEX,local_music_pool,index_file

OUTPUT_LABELS = {"直接生成 MP4（本地音乐/动效）": "ffmpeg", "生成剪映草稿（原生曲库/特效）": "jianying"}
MUSIC_LABELS = {"剪映云端曲库": "cloud", "本地音乐文件": "local", "不加配乐": "none"}
AUTO_MUSIC_LABELS={"本地曲库（推荐）":"local_index","剪映云端曲库（旧方案）":"cloud"}
SELECTION_LABELS = {"自动包装（随机 5 层动效 + 多首音乐）": "auto", "手动指定（高级）": "manual"}
PROFILE_LABELS = {"产品可辨：3 种氛围 + 2 种动感": GENTLE_PROFILE, "旧方案（兼容旧计划）": "legacy"}
CURRENT_BATCH = "当前批次（最新生成）"
ALL_HISTORY = "全部历史（仅查看 / 选中后生成）"


class FolderMixApp(MixCutApp):
    """Reuse the existing queue, log, stop and close behavior; replace the workflow UI."""
    def __init__(self, root, config_path):
        self.root = root
        self.service = FolderService(config_path)
        self.runner = JobRunner(self.service.config_path)
        self.events = queue.Queue()
        self.mutating, self.forms = [], []
        self.row_maps, self.preview_tokens, self.photos = {}, {}, {}
        self.closing = False
        self.root.title("MixCut · 文件夹批量拼接" + ("（"+self.service.project.config.get("demo_label","测试素材")+"）" if self.service.project.config.get("demo") else ""))
        root.geometry("1180x870"); root.minsize(1040,760); root.configure(bg=BG)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self._style()
        ttk.Style(root).configure("Small.TButton", padding=(7,5))
        root.columnconfigure(0,weight=1); root.rowconfigure(2,weight=1)
        self._header()
        self._folder_stats()
        self.panes = ttk.Panedwindow(root, orient="vertical")
        self.panes.grid(row=2,column=0,sticky="nsew",padx=18,pady=(0,8))
        self.tabs=ttk.Notebook(self.panes); self.panes.add(self.tabs,weight=4)
        self.count=tk.StringVar(value=str(self.service.project.config["mix"]["count"]))
        self.slot_vars=[]
        cfg=settings(self.service.project)
        for slot in cfg["slots"]:
            self.slot_vars.append(self.new_slot(slot))
        self.image_folder=tk.StringVar(value=cfg["image_folder"])
        self.image_seconds=tk.StringVar(value=str(cfg["image_seconds"]))
        self._inputs()
        self._options()
        self._plans()
        self._logs()
        self.status_var=tk.StringVar(value="就绪 · 放好分类素材，保存后生成计划并出片")
        footer=ttk.Frame(root,padding=(22,4,22,10)); footer.grid(row=3,column=0,sticky="ew")
        ttk.Label(footer,textvariable=self.status_var,style="Muted.TLabel").pack(side="left")
        root.bind_all("<MouseWheel>",self._wheel,add="+")
        self.refresh()
        root.after(100,self.poll)

    def _folder_stats(self):
        bar=ttk.Frame(self.root,padding=(18,12)); bar.grid(row=1,column=0,sticky="ew")
        self.stats={}
        for index,(key,label) in enumerate([("videos","视频素材"),("images","产品图片"),("plans","组合计划"),("outputs","MP4 / 剪映草稿")]):
            bar.columnconfigure(index,weight=1,uniform="stats")
            card=ttk.Frame(bar,padding=(16,9),style="Stat.TFrame"); card.grid(row=0,column=index,sticky="ew",padx=(0,10))
            value=tk.StringVar(value="0"); self.stats[key]=value
            ttk.Label(card,textvariable=value,style="Number.TLabel").pack(anchor="w")
            ttk.Label(card,text=label,style="Stat.TLabel").pack(anchor="w")
        ttk.Label(bar,text="文件夹拼接\n无需 AI 服务",style="Muted.TLabel").grid(row=0,column=4,padx=10)

    def new_slot(self, slot):
        return {"id":slot["id"],"label":tk.StringVar(value=slot["label"]),"folder":tk.StringVar(value=slot["folder"]),"count":tk.StringVar()}

    def _inputs(self):
        self.input_tab=ScrollPage(self.tabs); self.tabs.add(self.input_tab,text="① 素材与顺序")
        page=self.input_tab.body; page.columnconfigure(0,weight=1)
        ttk.Label(page,text="每个视频段落随机选 1 条，按下列顺序拼接，再从图片文件夹随机选 4 张不同的图。",style="Muted.TLabel").grid(row=0,column=0,sticky="w",pady=(0,12))
        box=ttk.LabelFrame(page,text="视频段落 · 可更换文件夹、编辑名称及上下调整顺序",padding=10)
        box.grid(row=1,column=0,sticky="ew"); box.columnconfigure(0,weight=1)
        self.slots_frame=ttk.Frame(box); self.slots_frame.grid(row=0,column=0,sticky="ew")
        self.rebuild_slots()
        self.button(box,"添加视频段落",self.add_slot,mutation=True).grid(row=1,column=0,sticky="w",pady=(10,0))
        images=ttk.LabelFrame(page,text="末尾产品图 · 每条抽 4 张，依次播放",padding=12)
        images.grid(row=2,column=0,sticky="ew",pady=(14,0)); images.columnconfigure(0,weight=1)
        entry=ttk.Entry(images,textvariable=self.image_folder); entry.grid(row=0,column=0,sticky="ew"); self.forms.append(entry)
        self.button(images,"选择文件夹",lambda:self.choose_folder(self.image_folder),mutation=True).grid(row=0,column=1,padx=8)
        self.button(images,"打开",lambda:self.open_folder(self.image_folder)).grid(row=0,column=2)
        row=ttk.Frame(images); row.grid(row=1,column=0,columnspan=3,sticky="ew",pady=(10,0))
        ttk.Label(row,text="每张图 / 秒").pack(side="left")
        spin=ttk.Spinbox(row,textvariable=self.image_seconds,from_=1,to=3600,width=7); spin.pack(side="left",padx=8); self.forms.append(spin)
        self.tail_hint=tk.StringVar(); ttk.Label(row,textvariable=self.tail_hint,style="Muted.TLabel").pack(side="left",padx=10)
        self.image_seconds.trace_add("write",lambda *args:self.update_tail_hint())
        self.update_tail_hint()
        ttk.Label(page,text="支持 MP4 / MOV / MKV / WEBM / AVI，读取文件夹第一层。源文件只读取，名称不同但内容相同的产品图会排重。",style="Muted.TLabel",wraplength=1000).grid(row=3,column=0,sticky="w",pady=(10,0))
        fixed=ttk.Frame(self.input_tab,padding=(18,10)); fixed.grid(row=1,column=0,columnspan=2,sticky="ew")
        self.button(fixed,"保存配置",self.save_all,mutation=True).pack(side="left")
        self.button(fixed,"刷新素材数量",self.refresh).pack(side="left",padx=8)
        ttk.Label(fixed,text="出片数").pack(side="left",padx=(16,6))
        spin=ttk.Spinbox(fixed,textvariable=self.count,from_=1,to=1000,width=6); spin.pack(side="left"); self.forms.append(spin)
        self.batch_button=self.button(fixed,"生成计划并出片",self.batch,primary=True,mutation=True)
        self.batch_button.pack(side="right")

    def rebuild_slots(self):
        for widget in self.slots_frame.winfo_children(): widget.destroy()
        self.forms=[widget for widget in self.forms if widget.winfo_exists()]
        self.mutating=[widget for widget in self.mutating if widget.winfo_exists()]
        self.slots_frame.columnconfigure(2,weight=1)
        for index,slot in enumerate(self.slot_vars):
            ttk.Label(self.slots_frame,text=str(index+1),width=2).grid(row=index,column=0,pady=5)
            label=ttk.Entry(self.slots_frame,textvariable=slot["label"],width=13)
            label.grid(row=index,column=1,padx=(0,8)); self.forms.append(label)
            path=ttk.Entry(self.slots_frame,textvariable=slot["folder"])
            path.grid(row=index,column=2,sticky="ew",padx=(0,6)); self.forms.append(path)
            ttk.Label(self.slots_frame,textvariable=slot["count"],width=6,style="Muted.TLabel").grid(row=index,column=3)
            actions=[("选择",lambda v=slot["folder"]:self.choose_folder(v),True),
                ("打开",lambda v=slot["folder"]:self.open_folder(v),False),
                ("↑",lambda i=index:self.move_slot(i,-1),True),("↓",lambda i=index:self.move_slot(i,1),True),
                ("移除",lambda i=index:self.remove_slot(i),True)]
            for column,(text,command,mutation) in enumerate(actions,4):
                button=ttk.Button(self.slots_frame,text=text,command=command,style="Small.TButton",width=4)
                button.grid(row=index,column=column,padx=2)
                if mutation:self.mutating.append(button)

    def add_slot(self):
        self.slot_vars.append(self.new_slot({"id":"slot_"+uuid.uuid4().hex[:8],"label":"新增段落","folder":""}))
        self.rebuild_slots(); self.refresh()

    def remove_slot(self,index):
        if len(self.slot_vars)==1:self.alert("请至少保留一个视频段落。");return
        self.slot_vars.pop(index);self.rebuild_slots();self.refresh()

    def move_slot(self,index,offset):
        target=index+offset
        if 0<=target<len(self.slot_vars):
            self.slot_vars[index],self.slot_vars[target]=self.slot_vars[target],self.slot_vars[index]
            self.rebuild_slots();self.refresh()

    def choose_folder(self,variable):
        path=filedialog.askdirectory(parent=self.root,title="选择已准备好的素材文件夹",mustexist=True)
        if path:variable.set(str(Path(path).resolve()));self.refresh()

    def open_folder(self,variable):
        if variable.get().strip():self.open_path(self.service.project.asset(variable.get().strip()))

    def update_tail_hint(self):
        try:
            seconds=float(self.image_seconds.get())
            self.tail_hint.set(f"4 张合计 {seconds*4/60:g} 分钟；常用 240–300 秒 / 张")
        except ValueError:self.tail_hint.set("请填写每张图的播放秒数")

    def _options(self):
        self.option_tab=ScrollPage(self.tabs);self.tabs.add(self.option_tab,text="② 出片设置")
        page=self.option_tab.body;page.columnconfigure(0,weight=1)
        cfg=self.service.project.config["render"]
        mode=ttk.LabelFrame(page,text="输出方式与叠加效果",padding=14)
        mode.grid(row=0,column=0,sticky="ew",pady=(0,14))
        self.output_mode=tk.StringVar(value=next((label for label,value in OUTPUT_LABELS.items() if value==cfg.get("output_mode","ffmpeg")),next(iter(OUTPUT_LABELS))))
        combo=ttk.Combobox(mode,textvariable=self.output_mode,values=list(OUTPUT_LABELS),state="readonly",width=42)
        combo.pack(anchor="w");self.forms.append(combo)
        ttk.Label(mode,text="视频保持原声 100%；配乐和五层特效仅覆盖尾图。四角产品图可全程贴边显示；人物墨镜从第一段开头结束后生效。",style="Muted.TLabel",wraplength=1000).pack(anchor="w",pady=(8,0))
        box=ttk.LabelFrame(page,text="本地音乐与特效 · 仅图片尾部",padding=14);box.grid(row=1,column=0,sticky="ew");box.columnconfigure(1,weight=1)
        self.local_box=box
        self.music_path=tk.StringVar(value=cfg.get("music",""));self.effect_path=tk.StringVar(value=cfg.get("effect_video",""))
        self.path_field(box,0,"背景音乐（可选）",self.music_path,[("音频","*.mp3 *.wav *.m4a *.flac *.ogg"),("所有文件","*.*")])
        ttk.Label(box,text="留空时图片段静音；选择音乐后只在四张图片期间循环播放。",style="Muted.TLabel").grid(row=1,column=0,columnspan=3,sticky="w",pady=(0,10))
        self.music_volume=tk.DoubleVar(value=cfg.get("music_volume",.18));self.original_volume=tk.DoubleVar(value=1)
        audio=ttk.Frame(box);audio.grid(row=2,column=0,columnspan=3,sticky="ew",pady=8)
        self.slider(audio,"音乐音量",self.music_volume).pack(side="left",fill="x",expand=True,padx=(0,20))
        ttk.Label(audio,text="前面视频原声：100%（固定）",style="Muted.TLabel").pack(side="right")
        meteor=cfg.get("meteor",{});self.meteor=tk.BooleanVar(value=meteor.get("enabled",True));self.intensity=tk.DoubleVar(value=meteor.get("intensity",.55))
        self.scope=tk.StringVar(value="只在尾图")
        row=ttk.Frame(box);row.grid(row=3,column=0,columnspan=3,sticky="ew",pady=8)
        check=ttk.Checkbutton(row,text="启用流星 / 氛围效果",variable=self.meteor);check.pack(side="left");self.mutating.append(check)
        ttk.Label(row,text="范围：仅四张产品图",style="Muted.TLabel").pack(side="left",padx=18)
        self.slider(row,"强度",self.intensity,150).pack(side="right")
        self.path_field(box,4,"透明动效（可选）",self.effect_path,[("动效视频","*.mov *.webm *.mp4")])
        self._native_options(page,cfg)
        self._corner_options(page,cfg)
        output=ttk.LabelFrame(page,text="画幅与编码",padding=14);output.grid(row=3,column=0,sticky="ew",pady=14)
        self.resolution=tk.StringVar(value=f"{cfg['width']} × {cfg['height']}");self.fps=tk.StringVar(value=str(cfg['fps']))
        ttk.Label(output,text="画幅").pack(side="left",padx=(0,8))
        combo=ttk.Combobox(output,textvariable=self.resolution,values=list(dict.fromkeys([self.resolution.get(),"720 × 1280","1080 × 1920","540 × 960"])),width=18,state="readonly");combo.pack(side="left");self.forms.append(combo)
        ttk.Label(output,text="帧率").pack(side="left",padx=(20,8))
        combo=ttk.Combobox(output,textvariable=self.fps,values=["24","25","30"],width=7);combo.pack(side="left");self.forms.append(combo)
        fixed=ttk.Frame(self.option_tab,padding=(18,10));fixed.grid(row=1,column=0,columnspan=2,sticky="ew")
        self.button(fixed,"保存配置",self.save_all,primary=True,mutation=True).pack(side="left")
        self.button(fixed,"应用到本批计划",self.apply_pending,mutation=True).pack(side="left",padx=10)
        ttk.Label(fixed,text="仅更新当前查看批次；历史批次及已完成 MP4 保留。",style="Muted.TLabel").pack(side="left")
        self.output_mode.trace_add("write",lambda *args:self._output_changed())
        self._output_changed()

    def _corner_options(self,page,cfg):
        options=corner_settings(cfg)
        box=ttk.LabelFrame(page,text="四角产品图 · 全程贴边",padding=14)
        self.corner_box=box
        box.grid(row=2,column=0,sticky="ew",pady=(14,0));box.columnconfigure(1,weight=1)
        self.corners_enabled=tk.BooleanVar(value=options["enabled"])
        self.corner_folder=tk.StringVar(value=options["folder"])
        self.corner_width=tk.StringVar(value=f"{float(options['width_percent']):g}")
        self.corner_max_height=float(options["max_height_percent"])
        check=ttk.Checkbutton(box,text="开启四角产品图（包括开头、拼接视频和末尾长图）",variable=self.corners_enabled)
        check.grid(row=0,column=0,columnspan=3,sticky="w");self.mutating.append(check)
        ttk.Label(box,text="图片文件夹").grid(row=1,column=0,sticky="w",pady=10,padx=(0,10))
        entry=ttk.Entry(box,textvariable=self.corner_folder);entry.grid(row=1,column=1,sticky="ew");self.forms.append(entry)
        self.button(box,"选择文件夹",lambda:self.choose_folder(self.corner_folder),mutation=True).grid(row=1,column=2,padx=(10,0))
        row=ttk.Frame(box);row.grid(row=2,column=0,columnspan=3,sticky="ew")
        ttk.Label(row,text="每张图最大宽度 / 画面宽度 %").pack(side="left")
        spin=ttk.Spinbox(row,textvariable=self.corner_width,from_=5,to=30,increment=1,width=7)
        spin.pack(side="left",padx=10);self.forms.append(spin)
        ttk.Label(row,text="默认 18%，保持图片比例；边距固定 0，贴紧四角。",style="Muted.TLabel").pack(side="left")
        ttk.Label(box,text=f"文件夹留空时使用素材页的产品图文件夹。每条随机选 4 张不同图片、固定四角全程显示；图片高度最多为画面高度的 {self.corner_max_height:g}%。\n原有五层特效和墨镜放在内层内容，四角图放在外层；本地 MP4 和剪映草稿均支持。保存后生成新计划，或应用到选定批次。",style="Muted.TLabel",wraplength=990).grid(row=3,column=0,columnspan=3,sticky="w",pady=(10,0))

    def _native_options(self,page,cfg):
        options=native_settings(cfg)
        panel=ttk.LabelFrame(page,text="剪映自动包装 · 仅四张产品图",padding=14)
        panel.grid(row=1,column=0,sticky="ew");panel.columnconfigure(0,weight=1)
        self.selection_mode=tk.StringVar(value=next(label for label,value in SELECTION_LABELS.items() if value==options["selection_mode"]))
        combo=ttk.Combobox(panel,textvariable=self.selection_mode,values=list(SELECTION_LABELS),width=46,state="readonly")
        combo.grid(row=0,column=0,sticky="w");self.forms.append(combo)
        self.auto_box=ttk.Frame(panel);self.auto_box.grid(row=1,column=0,sticky="ew",pady=10)
        self._music_library_options(self.auto_box,options)
        profiles=ttk.Frame(self.auto_box);profiles.pack(fill="x",pady=(0,10))
        self.effect_profile=tk.StringVar(value=next(label for label,value in PROFILE_LABELS.items() if value==options["effect_profile"]))
        profile_combo=ttk.Combobox(profiles,textvariable=self.effect_profile,values=list(PROFILE_LABELS),state="readonly",width=40)
        profile_combo.pack(side="left");self.forms.append(profile_combo)
        self.button(profiles,"查看候选名单",self.show_effect_candidates).pack(side="left",padx=10)
        self.button(profiles,"打开实拍预览",self.open_effect_review).pack(side="left")
        self.effect_profile.trace_add("write",lambda *args:self._update_pool_hint())
        row=ttk.Frame(self.auto_box);row.pack(fill="x")
        ttk.Label(row,text="特效风格候选池").pack(side="left",padx=(0,14))
        self.effect_categories={name:tk.BooleanVar(value=name in options["effect_categories"]) for name in ["氛围","动感"]}
        self.include_vip=tk.BooleanVar(value=options["include_vip"])
        for name,var in [*self.effect_categories.items(),("包含 VIP 标记条目",self.include_vip)]:
            check=ttk.Checkbutton(row,text=name,variable=var);check.pack(side="left",padx=(0,16));self.mutating.append(check)
            var.trace_add("write",lambda *args:self._update_pool_hint())
        self.pool_hint=tk.StringVar();ttk.Label(self.auto_box,textvariable=self.pool_hint,style="Muted.TLabel").pack(anchor="w",pady=(10,4))
        ttk.Label(self.auto_box,text="从 40 种氛围中随机抽 3 种、18 种动感中随机抽 2 种，五层同时覆盖四图全长。效果可以浓郁，以产品图仍可辨认为准。\n模糊/变形等可调参数按英文名控制；无对应参数的效果用原生表现。先看预览，再应用到本批计划。风格为本地分组。",style="Muted.TLabel",wraplength=990).pack(anchor="w",pady=(0,10))
        levels=ttk.Frame(self.auto_box);levels.pack(fill="x")
        self.auto_strength=tk.DoubleVar(value=min(float(options["effect_strength"]),18)/100 if options["effect_profile"]==GENTLE_PROFILE else float(options["effect_strength"])/100)
        self.slider(levels,"可调特效参数强度",self.auto_strength).pack(side="left",fill="x",expand=True,padx=(0,20))
        self.slider(levels,"图片段配乐音量",self.music_volume).pack(side="left",fill="x",expand=True)
        self.manual_box=ttk.Frame(panel);self.manual_box.grid(row=1,column=0,sticky="ew",pady=10);self.manual_box.columnconfigure(0,weight=1)
        self._manual_options(self.manual_box,cfg)
        shared=ttk.Frame(panel);shared.grid(row=2,column=0,sticky="ew",pady=(8,0));shared.columnconfigure(1,weight=1)
        ttk.Label(shared,text="草稿保存目录").grid(row=0,column=0,sticky="w",padx=(0,12))
        entry=ttk.Entry(shared,textvariable=self.draft_root);entry.grid(row=0,column=1,sticky="ew");self.forms.append(entry)
        self.button(shared,"选择目录",lambda:self.choose_folder(self.draft_root),mutation=True).grid(row=0,column=2,padx=(10,0))
        self.face_cover=tk.BooleanVar(value=options.get("face_cover_enabled",False))
        face_box=ttk.LabelFrame(panel,text="人物墨镜开关（与图片尾部五效独立）",padding=10)
        face_box.grid(row=3,column=0,sticky="ew",pady=(12,0))
        check=ttk.Checkbutton(face_box,text="开启挡脸墨镜：拽酷墨镜",variable=self.face_cover)
        check.pack(anchor="w");self.mutating.append(check)
        ttk.Label(face_box,text="默认关闭。开启后跳过第一段开头，从第二段开始持续到成片末尾；原声不变。识别跟随由剪映完成，可能只匹配部分人脸。\n本机剪映当前将‘拽酷墨镜’标为‘不可商用’，资源权限以客户端显示为准。",style="Muted.TLabel",wraplength=950).pack(anchor="w",pady=(5,0))
        ttk.Label(panel,text="原生资源需在剪映中加载并导出 MP4；人物墨镜资源权限及跟随表现以客户端预览为准。",style="Muted.TLabel",wraplength=990).grid(row=4,column=0,sticky="w",pady=(10,0))
        self.native_box=panel
        self.selection_mode.trace_add("write",lambda *args:self._native_selection_changed())
        self._native_selection_changed()

    def _update_pool_hint(self):
        try:
            selected=[name for name,var in self.effect_categories.items() if var.get()]
            pool=effect_pool({"effect_categories":selected,"include_vip":self.include_vip.get(),"effect_profile":PROFILE_LABELS[self.effect_profile.get()]})
            counts=" / ".join(f"{name} {sum(item['category']==name for item in pool)}" for name in selected)
            songs=self.refresh_music_library()
            local=AUTO_MUSIC_LABELS[self.auto_music_source.get()]=="local_index"
            if local and songs is None:raise PipelineError(self.library_hint.get())
            number=len(songs) if local else len(music_pool())
            self.pool_hint.set(f"特效 {len(pool)} 种（{counts}）；{'本地' if local else '云端'}音乐 {number} 首，顺序铺满图片尾段，截断最后一首。")
        except PipelineError as error:self.pool_hint.set(str(error))

    def _music_library_options(self,parent,options):
        row=ttk.Frame(parent);row.pack(fill="x",pady=(0,8))
        ttk.Label(row,text="自动配乐来源").pack(side="left",padx=(0,10))
        self.auto_music_source=tk.StringVar(value=next((k for k,v in AUTO_MUSIC_LABELS.items() if v==options["music_source"]),next(iter(AUTO_MUSIC_LABELS))))
        combo=ttk.Combobox(row,textvariable=self.auto_music_source,values=list(AUTO_MUSIC_LABELS),state="readonly",width=28)
        combo.pack(side="left");self.forms.append(combo)
        self.local_music_index=tk.StringVar(value=options.get("local_music_index") or str(DEFAULT_INDEX))
        self.library_min_seconds=tk.StringVar(value="120")
        box=ttk.LabelFrame(parent,text="本地音乐库 · 复制后独立保存",padding=10);box.pack(fill="x",pady=(0,12));box.columnconfigure(1,weight=1)
        ttk.Label(box,text="曲库索引").grid(row=0,column=0,padx=(0,10))
        entry=ttk.Entry(box,textvariable=self.local_music_index);entry.grid(row=0,column=1,sticky="ew");self.forms.append(entry)
        self.button(box,"选择已有曲库",lambda:self.choose_file(self.local_music_index,[("曲库索引","*.json")]),mutation=True).grid(row=0,column=2,padx=(10,0))
        actions=ttk.Frame(box);actions.grid(row=1,column=0,columnspan=3,sticky="ew",pady=8)
        self.button(actions,"导入剪映缓存",self.import_cached_music,mutation=True).pack(side="left")
        self.button(actions,"导入音乐文件夹",self.import_music_folder,mutation=True).pack(side="left",padx=8)
        self.button(actions,"刷新曲库",self._update_pool_hint).pack(side="left")
        self.button(actions,"试听随机一首",self.preview_library_music).pack(side="left",padx=8)
        self.button(actions,"打开曲库",lambda:self.open_path(index_file(self.service.project,{"local_music_index":self.local_music_index.get()}).parent)).pack(side="left")
        settings=ttk.Frame(box);settings.grid(row=2,column=0,columnspan=3,sticky="ew")
        ttk.Label(settings,text="导入最短时长 / 秒").pack(side="left")
        spin=ttk.Spinbox(settings,textvariable=self.library_min_seconds,from_=0,to=600,width=7);spin.pack(side="left",padx=8);self.forms.append(spin)
        self.button(settings,"仅将本批配乐换为本地",self.localize_batch_music,mutation=True).pack(side="right")
        self.library_hint=tk.StringVar();ttk.Label(box,textvariable=self.library_hint,style="Muted.TLabel",wraplength=930).grid(row=3,column=0,columnspan=3,sticky="w",pady=(8,0))
        ttk.Label(box,text="缓存音频用编号和时长显示，原始文件名保存在索引中。导入校正封装并完整解码；续跑沿用已选歌曲。仅在四图尾段播放，前面视频保持原声。",style="Muted.TLabel",wraplength=930).grid(row=4,column=0,columnspan=3,sticky="w",pady=(4,0))
        self.auto_music_source.trace_add("write",lambda *args:self._update_pool_hint())
        self.local_music_index.trace_add("write",lambda *args:self._update_pool_hint())

    def refresh_music_library(self):
        try:
            songs=local_music_pool(self.service.project,{"local_music_index":self.local_music_index.get()})
            self.library_hint.set(f"本地曲库 {len(songs)} 首，合计 {sum(s['seconds'] for s in songs)/3600:.1f} 小时；本地文件不依赖云端音乐 ID。")
            return songs
        except (PipelineError,OSError,ValueError) as error:
            self.library_hint.set(str(error));return None

    def start_music_import(self,source):
        try:
            self._guard();seconds=float(self.library_min_seconds.get())
            if not 0<=seconds<=86400:raise ValueError()
            target=index_file(self.service.project,{"local_music_index":self.local_music_index.get()}).parent
            self.local_music_index.set(str(target/"index.json"))
            self.start_job("导入本地音乐库",[["harvest-music","--source",str(source),"--target",str(target),"--min-seconds",str(seconds)]])
        except ValueError:self.alert("导入最短时长需填写 0–86400 秒之间的数字。")
        except PipelineError as error:self.alert(error)

    def import_cached_music(self):
        from .harvest import DEFAULT_SOURCE
        self.start_music_import(DEFAULT_SOURCE)

    def import_music_folder(self):
        path=filedialog.askdirectory(parent=self.root,title="选择要复制到本地曲库的音乐文件夹",mustexist=True)
        if path:self.start_music_import(path)

    def preview_library_music(self):
        import random
        songs=self.refresh_music_library()
        if songs:
            song=random.choice(songs)
            self.note(f"试听：{song['name']} · {duration_text(song['seconds'])}")
            self.open_path(song["path"])
        else:self.alert(self.library_hint.get())

    def localize_batch_music(self):
        try:
            batch=self.viewing_batch();self.auto_music_source.set(next(k for k,v in AUTO_MUSIC_LABELS.items() if v=="local_index"))
            if self.save_all():self.start_job("仅替换本批配乐",[["localize-music","--batch",batch["batch_id"],"--index",str(index_file(self.service.project,{"local_music_index":self.local_music_index.get()}))]])
        except PipelineError as error:self.alert(error)

    def show_effect_candidates(self):
        from tkinter import messagebox
        groups=gentle_groups()["groups"]
        messagebox.showinfo("产品可辨候选（待预览确认）", f"氛围候选 {len(groups['氛围'])} 种（每条抽 3 种）：\n"+"、".join(groups["氛围"])+f"\n\n动感候选 {len(groups['动感'])} 种（每条抽 2 种）：\n"+"、".join(groups["动感"])+"\n\n允许效果浓郁；以仍能认出产品图为准。已有计划保持原样。",parent=self.root)

    def open_effect_review(self):
        folder=ROOT/"examples/gentle_effect_review"
        video=folder/"特效候选预览.mp4"
        self.open_path(video if video.is_file() else folder)

    def _native_selection_changed(self):
        auto=SELECTION_LABELS[self.selection_mode.get()]=="auto"
        self.auto_box.grid() if auto else self.auto_box.grid_remove()
        self.manual_box.grid_remove() if auto else self.manual_box.grid()
        self._update_pool_hint()
        if hasattr(self,"native_box"):self._output_changed()

    def _manual_options(self,page,cfg):
        options=native_settings(cfg)
        box=ttk.LabelFrame(page,text="手动音乐与单个特效（高级）",padding=10)
        box.grid(row=0,column=0,sticky="ew");box.columnconfigure(1,weight=1)
        self.music_source=tk.StringVar(value=next((label for label,value in MUSIC_LABELS.items() if value==options["music_source"]),"不加配乐"))
        ttk.Label(box,text="配乐来源").grid(row=0,column=0,sticky="w",padx=(0,12))
        combo=ttk.Combobox(box,textvariable=self.music_source,values=list(MUSIC_LABELS),state="readonly",width=20)
        combo.grid(row=0,column=1,sticky="w");self.forms.append(combo)
        self.cloud_box=ttk.Frame(box);self.cloud_box.grid(row=1,column=0,columnspan=3,sticky="ew",pady=10)
        self.cloud_box.columnconfigure(1,weight=1)
        music=options.get("cloud_music") or {}
        self.cloud_name=tk.StringVar(value=music.get("name",""));self.cloud_id=tk.StringVar(value=music.get("id",""));self.cloud_seconds=tk.StringVar(value=str(music.get("seconds","")))
        ttk.Label(self.cloud_box,text="音乐标题").grid(row=0,column=0,padx=(0,12))
        entry=ttk.Entry(self.cloud_box,textvariable=self.cloud_name);entry.grid(row=0,column=1,sticky="ew");self.forms.append(entry)
        self.button(self.cloud_box,"搜索剪映曲库",self.pick_music,mutation=True).grid(row=0,column=2,padx=(10,0))
        details=ttk.Frame(self.cloud_box);details.grid(row=1,column=0,columnspan=3,sticky="ew",pady=(8,0))
        for label,variable,width in [("音乐 ID",self.cloud_id,27),("原曲秒数",self.cloud_seconds,9)]:
            ttk.Label(details,text=label).pack(side="left",padx=(0,8))
            entry=ttk.Entry(details,textvariable=variable,width=width);entry.pack(side="left",padx=(0,18));self.forms.append(entry)
        self.native_local_box=ttk.Frame(box);self.native_local_box.grid(row=1,column=0,columnspan=3,sticky="ew",pady=10);self.native_local_box.columnconfigure(1,weight=1)
        self.path_field(self.native_local_box,0,"本地配乐",self.music_path,[("音频","*.mp3 *.wav *.m4a *.flac *.ogg")])
        level=self.slider(box,"图片段配乐音量",self.music_volume)
        level.grid(row=2,column=0,columnspan=3,sticky="ew",pady=8)
        effectrow=ttk.Frame(box);effectrow.grid(row=3,column=0,columnspan=3,sticky="ew",pady=8)
        self.native_effect=tk.BooleanVar(value=options.get("effect_enabled",True));self.native_effect_key=options.get("effect_key","流星雨")
        self.native_effect_name=tk.StringVar(value=next((item["name"] for item in catalog("effects") if item["key"]==self.native_effect_key),self.native_effect_key))
        check=ttk.Checkbutton(effectrow,text="图片段启用剪映特效",variable=self.native_effect);check.pack(side="left");self.mutating.append(check)
        ttk.Label(effectrow,textvariable=self.native_effect_name).pack(side="left",padx=18)
        self.button(effectrow,"选择原生特效",self.pick_effect,mutation=True).pack(side="right")
        self.draft_root=tk.StringVar(value=options.get("draft_root",""))
        self.music_source.trace_add("write",lambda *args:self._music_source_changed())
        self._music_source_changed()

    def _music_source_changed(self):
        source=MUSIC_LABELS[self.music_source.get()]
        self.cloud_box.grid() if source=="cloud" else self.cloud_box.grid_remove()
        self.native_local_box.grid() if source=="local" else self.native_local_box.grid_remove()

    def _output_changed(self):
        native=OUTPUT_LABELS[self.output_mode.get()]=="jianying"
        self.native_box.grid() if native else self.native_box.grid_remove()
        self.local_box.grid_remove() if native else self.local_box.grid()
        automatic=native and SELECTION_LABELS[self.selection_mode.get()]=="auto"
        self.batch_button.configure(text="自动包装并生成批量草稿" if automatic else "生成计划与批量草稿" if native else "生成计划并输出 MP4")

    def pick_music(self):
        item=AssetPicker(self.root,"music").result
        if item:
            self.cloud_name.set(item["name"]);self.cloud_id.set(item["id"]);self.cloud_seconds.set(str(item["seconds"]))

    def pick_effect(self):
        item=AssetPicker(self.root,"effects").result
        if item:self.native_effect_key=item["key"];self.native_effect_name.set(item["name"])

    def _plans(self):
        self.plan_tab=ttk.Frame(self.tabs,padding=16);self.tabs.add(self.plan_tab,text="③ 计划与成片")
        page=self.plan_tab;page.columnconfigure(0,weight=1);page.rowconfigure(2,weight=1)
        row=ttk.Frame(page);row.grid(row=0,column=0,sticky="ew",pady=(0,12))
        self.button(row,"只生成计划",self.plan,mutation=True).pack(side="left")
        self.button(row,"继续本批计划",self.resume,primary=True,mutation=True).pack(side="left",padx=10)
        self.button(row,"打开所选结果",self.open_plan_output).pack(side="left")
        self.button(row,"打开成片目录",lambda:self.open_path(self.service.project.outputs)).pack(side="right")
        self.button(row,"打开草稿目录",self.open_draft_folder).pack(side="right",padx=8)
        batch_row=ttk.Frame(page);batch_row.grid(row=1,column=0,sticky="ew",pady=(0,12))
        self.button(batch_row,"生成本批复合草稿",self.compound,primary=True,mutation=True).pack(side="left")
        self.button(batch_row,"打开本批草稿目录",self.open_compound).pack(side="left",padx=10)
        self.button(batch_row,"生成选中计划复合草稿",self.compound_selected,mutation=True).pack(side="left")
        self.button(batch_row,"复制本批草稿名",self.copy_compound_name).pack(side="left",padx=10)
        scope_row=ttk.Frame(page);scope_row.grid(row=2,column=0,sticky="ew",pady=(0,10))
        ttk.Label(scope_row,text="计划范围").pack(side="left",padx=(0,10))
        self.plan_scope=tk.StringVar(value=CURRENT_BATCH);self.batch_labels={CURRENT_BATCH:None}
        self.batch_combo=ttk.Combobox(scope_row,textvariable=self.plan_scope,state="readonly",width=55)
        self.batch_combo.pack(side="left");self.forms.append(self.batch_combo)
        self.batch_combo.bind("<<ComboboxSelected>>",lambda event:self.refresh())
        self.batch_hint=tk.StringVar();ttk.Label(scope_row,textvariable=self.batch_hint,style="Muted.TLabel").pack(side="left",padx=10)
        frame,self.plan_tree=self.tree(page,[("id","计划编号",220),("body","视频部分",120),("tail","四张图合计",150),("status","状态",120)])
        page.rowconfigure(2,weight=0);page.rowconfigure(3,weight=1)
        frame.grid(row=3,column=0,sticky="nsew");self.plan_tree.bind("<Double-1>",lambda event:self.open_plan_output())
        self.compound_hint=tk.StringVar()
        ttk.Label(page,textvariable=self.compound_hint,style="Muted.TLabel",wraplength=1030).grid(row=4,column=0,sticky="w",pady=(10,0))

    def values(self):
        width,height=self.resolution.get().split("×")
        return {"slots":[{"id":slot["id"],"label":slot["label"].get(),"folder":slot["folder"].get()} for slot in self.slot_vars],
            "image_folder":self.image_folder.get(),"image_seconds":self.image_seconds.get(),"count":self.count.get(),
            "width":width.strip(),"height":height.strip(),"fps":self.fps.get(),"music":self.music_path.get(),"effect_video":self.effect_path.get(),
            "music_volume":self.music_volume.get(),"original_audio_volume":self.original_volume.get(),"meteor_enabled":self.meteor.get(),
            "scope":"tail","intensity":self.intensity.get(),"output_mode":OUTPUT_LABELS[self.output_mode.get()],
            "corner_overlay":{"enabled":self.corners_enabled.get(),"folder":self.corner_folder.get().strip(),
                "width_percent":self.corner_width.get(),"max_height_percent":self.corner_max_height},
            "jianying":{"selection_mode":SELECTION_LABELS[self.selection_mode.get()],
                "effect_profile":PROFILE_LABELS[self.effect_profile.get()],"face_cover_enabled":self.face_cover.get(),
                "effect_categories":[name for name,var in self.effect_categories.items() if var.get()],"include_vip":self.include_vip.get(),"effect_count":5,"effect_strength":round(self.auto_strength.get()*100,2),
                "music_source":AUTO_MUSIC_LABELS[self.auto_music_source.get()] if SELECTION_LABELS[self.selection_mode.get()]=="auto" else MUSIC_LABELS[self.music_source.get()],"local_music_index":self.local_music_index.get().strip(),"cloud_music":{"id":self.cloud_id.get().strip(),"name":self.cloud_name.get().strip(),"seconds":self.cloud_seconds.get().strip()},
                "effect_enabled":self.native_effect.get(),"effect_key":self.native_effect_key,"draft_root":self.draft_root.get().strip()}}

    def open_draft_folder(self):
        root=self.service.project.asset(self.draft_root.get().strip()) if self.draft_root.get().strip() else self.service.project.path/"07_剪映草稿"
        root.mkdir(parents=True,exist_ok=True);self.open_path(root)

    def save_all(self):
        try:
            self._guard();self.service.save(self.values());self.refresh();self.note("文件夹、顺序与出片设置已保存")
            return True
        except (PipelineError,OSError,ValueError,KeyError) as error:self.alert(error);return False

    def batch(self):
        if self.save_all():
            self.plan_scope.set(CURRENT_BATCH)
            self.tabs.select(self.plan_tab)
            self.start_job("文件夹批量拼接",[["plan","--count",self.count.get()],*self.output_commands()])

    def output_commands(self,scope_args=None):
        scope_args=scope_args or []
        return [["render",*scope_args],["batch-draft",*scope_args]] if OUTPUT_LABELS[self.output_mode.get()]=="jianying" else [["render",*scope_args]]

    def viewing_batch(self):
        if self.plan_scope.get()==ALL_HISTORY:
            raise PipelineError("当前正在查看全部历史。请选择一个批次；或选中计划后点击‘生成选中计划复合草稿’。")
        return get_batch(self.service.project,self.batch_labels.get(self.plan_scope.get()))

    def resume(self):
        try:
            batch=self.viewing_batch()
            self.start_job(f"续跑本批 {batch['count']} 条",self.output_commands(["--batch",batch["batch_id"]]))
        except PipelineError as error:self.alert(error)

    def compound(self):
        try:
            batch=self.viewing_batch()
            self.start_job(f"生成本批复合草稿（{batch['count']} 条）",[["batch-draft","--batch",batch["batch_id"]]])
        except PipelineError as error:self.alert(error)

    def compound_selected(self):
        selection=list(self.plan_tree.selection())
        if not selection:
            self.alert("请先在计划列表中选中要合成的计划，可按 Ctrl / Shift 多选。");return
        self.start_job(f"生成选中 {len(selection)} 条复合草稿",[["batch-draft",*selection]])

    def current_compound_result(self):
        batch=self.viewing_batch()
        result=batch_compound(self.service.project,batch["batch_id"])
        if not result or not Path(result["draft_path"]).is_dir():
            raise PipelineError("当前批次尚未生成复合草稿，请先点击‘生成本批复合草稿’。")
        return result

    def open_compound(self):
        try:
            result=self.current_compound_result()
            self.open_path(Path(result["draft_path"]))
            self.note("本批剪映草稿名："+Path(result["draft_path"]).name+"；在剪映首页打开同名草稿，目录："+result["draft_path"])
        except PipelineError as error:self.alert(error)

    def copy_compound_name(self):
        try:
            name=Path(self.current_compound_result()["draft_path"]).name
            self.root.clipboard_clear();self.root.clipboard_append(name)
            self.note("已复制本批草稿名；回到剪映首页搜索："+name)
        except PipelineError as error:self.alert(error)

    def plan(self):
        if self.save_all():
            self.plan_scope.set(CURRENT_BATCH)
            self.start_job("生成文件夹组合",[["plan","--count",self.count.get()]])

    def apply_pending(self):
        try:
            batch=self.viewing_batch()
            if self.save_all():
                count=self.service.apply_render_to_pending(batch["batch_id"]);self.refresh();self.note(f"已更新本批 {count} 个计划，保留视频与四张图；历史批次未修改")
        except (PipelineError,OSError,ValueError) as error:self.alert(error)

    def refresh_batch_choices(self):
        from datetime import datetime,timezone,timedelta
        labels={CURRENT_BATCH:None}
        for batch in list_batches(self.service.project):
            date=datetime.fromisoformat(batch["created_at"]).astimezone(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M:%S")
            labels[f"{date} · {batch['count']} 条 · {batch['batch_id'][:6]}"]=batch["batch_id"]
        self.batch_labels=labels
        self.batch_combo.configure(values=[*labels,ALL_HISTORY])
        if self.plan_scope.get() not in [*labels,ALL_HISTORY]:self.plan_scope.set(CURRENT_BATCH)

    def visible_plan_rows(self):
        return self.service.plan_rows(self.batch_labels.get(self.plan_scope.get()),all_history=self.plan_scope.get()==ALL_HISTORY)

    def refresh_plans(self):
        selected=self.plan_tree.selection();self.plan_tree.delete(*self.plan_tree.get_children())
        labels={"pending":"待开始","running":"运行中","complete":"MP4 已完成","draft_ready":"草稿已生成","failed":"待重试"}
        rows=self.visible_plan_rows();mapping={row["id"]:row for row in rows}
        for row in rows:
            self.plan_tree.insert("","end",iid=row["id"],values=(row["id"],duration_text(row["body_seconds"]),duration_text(row["tail_seconds"]),labels.get(row["status"],row["status"])))
        self.row_maps["plans"]=mapping
        if set(selected).intersection(mapping):self.plan_tree.selection_set([i for i in selected if i in mapping])

    def refresh(self):
        try:
            self.service.reload()
            self.header_subtitle.set(f"文件夹批量拼接  /  {self.service.project.keyword}")
            videos=0
            for slot in self.slot_vars:
                count=len(files_in(self.service.project,slot["folder"].get().strip(),VIDEO_EXTS))
                slot["count"].set(f"{count} 条");videos+=count
            self.stats["videos"].set(str(videos))
            self.stats["images"].set(str(len(files_in(self.service.project,self.image_folder.get().strip(),IMAGE_EXTS))))
            self.refresh_batch_choices()
            rows=self.visible_plan_rows();history_count=len(self.service.plan_rows(all_history=True))
            self.stats["plans"].set(f"{len(rows)} / {history_count}")
            self.batch_hint.set(f"当前显示 {len(rows)} 条；历史共 {history_count} 条")
            if self.plan_scope.get()==ALL_HISTORY:
                self.compound_hint.set("历史仅用于查看；可多选计划生成指定复合草稿。新计划默认单独成批。")
            else:
                result=batch_compound(self.service.project,self.batch_labels.get(self.plan_scope.get()))
                self.compound_hint.set("本批草稿："+result["draft_path"]+"；请在剪映首页打开同名草稿。" if result else "本批尚无复合草稿。生成后会显示准确名称和目录；历史批次保留。")
            self.stats["outputs"].set(f"{sum(row['status']=='complete' for row in rows)} / {sum(row['status']=='draft_ready' for row in rows)}")
            self.refresh_plans()
        except (PipelineError,OSError,ValueError) as error:self.alert(error)
