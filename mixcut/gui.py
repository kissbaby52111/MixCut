"""Native Tk interface for the existing local pipeline. No web server required."""
from __future__ import annotations
from datetime import datetime
import os
from pathlib import Path
import queue
import subprocess
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from urllib.parse import urlparse

from .common import ROOT, ROLES, PipelineError, read_json, runtime_tool, media_info, object_hash
from .ui_service import UiService, JobRunner

BG = "#f3f6fb"
INK = "#20334d"
MUTED = "#66768c"
BLUE = "#2368d1"
SCOPE_LABELS = {"全片": "all", "只在主体": "body", "只在尾图": "tail"}

def checkmark_style(root, style):
    """Replace clam's cross indicator while keeping native checkbutton behavior."""
    element = "MixCut.check.indicator"
    if element in style.element_names():
        return
    size = max(16, round(18 * float(root.tk.call("tk", "scaling")) / (4 / 3)))
    def icon(fill, border, mark="", ink="white"):
        result = tk.PhotoImage(master=root, width=size, height=size)
        unit = size / 18
        edge = max(1, round(unit))
        result.put(border, to=(edge,edge,size-edge,size-edge))
        result.put(fill, to=(2*edge,2*edge,size-2*edge,size-2*edge))
        points = [(4,8),(7,11),(13,5)] if mark == "check" else [(4,9),(13,9)] if mark else []
        for (x1,y1),(x2,y2) in zip(points,points[1:]):
            steps = max(abs(x2-x1),abs(y2-y1)) * 2
            for step in range(steps+1):
                x = round((x1+(x2-x1)*step/steps)*unit)
                y = round((y1+(y2-y1)*step/steps)*unit)
                result.put(ink, to=(x,y,min(size,x+2*edge),min(size,y+2*edge)))
        return result
    off = icon("white", "#8091a8")
    on = icon(BLUE, BLUE, "check")
    disabled_off = icon("#e8edf5", "#b7c2d0")
    disabled_on = icon("#a5b2c3", "#a5b2c3", "check")
    mixed = icon(BLUE, BLUE, "dash")
    root._mixcut_check_images = (off,on,disabled_off,disabled_on,mixed)
    style.element_create(element,"image",off,("disabled","selected",disabled_on),
        ("disabled",disabled_off),("selected",on),("alternate",mixed),sticky="w")
    def replace(layout):
        return [(element if name == "Checkbutton.indicator" else name,
                 {key:replace(value) if key == "children" else value for key,value in options.items()})
                for name,options in layout]
    style.layout("TCheckbutton",replace(style.layout("TCheckbutton")))

def duration_text(value):
    if value is None:
        return "—"
    seconds = max(0, float(value))
    return f"{int(seconds)//60:02}:{seconds%60:04.1f}"

def preview_text(row, seconds):
    detail = row.get("description") or row.get("source") or ""
    if detail.startswith(("https://", "http://")):
        detail = "来源：" + urlparse(detail).netloc
    if len(detail) > 48:
        detail = detail[:48] + "…"
    return f"{row.get('category','')}  {duration_text(seconds)}\n{row['path'].name}" + ("\n" + detail if detail else "")

class ScrollPage(ttk.Frame):
    def __init__(self, master):
        super().__init__(master)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(self, bg=BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.body = ttk.Frame(self.canvas, padding=18)
        self.window_id = self.canvas.create_window((0,0), window=self.body, anchor="nw")
        self.body.bind("<Configure>", lambda event: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda event: self.canvas.itemconfigure(self.window_id, width=event.width))

class MixCutApp:
    def __init__(self, root: tk.Tk, config_path: Path):
        self.root = root
        self.service = UiService(config_path)
        self.runner = JobRunner(self.service.config_path)
        self.events = queue.Queue()
        self.mutating = []
        self.forms = []
        self.row_maps = {}
        self.preview_tokens = {}
        self.photos = {}
        self.closing = False
        self.root.title("MixCut · 本地电商混剪工具" + ("（测试素材）" if self.service.project.config.get("demo") else ""))
        self.root.geometry("1180x870")
        self.root.minsize(1000, 730)
        self.root.configure(bg=BG)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self._style()
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(2, weight=1)
        self._header()
        self._stats()
        self.panes = ttk.Panedwindow(root, orient="vertical")
        self.panes.grid(row=2, column=0, sticky="nsew", padx=18, pady=(0,8))
        self.tabs = ttk.Notebook(self.panes)
        self.panes.add(self.tabs, weight=4)
        self._dashboard()
        self._materials()
        self._clips()
        self._packaging()
        self._ai()
        self._logs()
        self.status_var = tk.StringVar(value="就绪 · 按顺序采集、审核、分类，再批量出片")
        footer = ttk.Frame(root, padding=(22,4,22,10))
        footer.grid(row=3, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        ttk.Label(footer, textvariable=self.status_var, style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(footer, text="本地运行", style="Muted.TLabel").grid(row=0, column=1, sticky="e")
        self.root.bind_all("<MouseWheel>", self._wheel, add="+")
        self.load_fields()
        self.refresh()
        self.root.after(100, self.poll)

    def _style(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        checkmark_style(self.root, style)
        style.configure(".", font=("Microsoft YaHei UI", 10), background=BG, foreground=INK)
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=INK)
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("Heading.TLabel", font=("Microsoft YaHei UI", 12, "bold"))
        style.configure("Header.TFrame", background="#14243d")
        style.configure("Title.TLabel", background="#14243d", foreground="white", font=("Microsoft YaHei UI", 22, "bold"))
        style.configure("Subtitle.TLabel", background="#14243d", foreground="#c3d0e3")
        style.configure("Stat.TFrame", background="white")
        style.configure("Stat.TLabel", background="white", foreground=MUTED)
        style.configure("Number.TLabel", background="white", foreground=INK, font=("Microsoft YaHei UI", 21, "bold"))
        style.configure("TLabelframe", background=BG, bordercolor="#d8e1ed")
        style.configure("TLabelframe.Label", background=BG, font=("Microsoft YaHei UI", 11, "bold"))
        style.configure("TButton", padding=(12,7), borderwidth=1)
        style.configure("Accent.TButton", background=BLUE, foreground="white", bordercolor=BLUE)
        style.map("Accent.TButton", background=[("disabled", "#b9c8dd"), ("active", "#1a55ad")], foreground=[("disabled", "#edf1f7")])
        style.configure("Danger.TButton", foreground="#b43b3b")
        style.configure("TEntry", padding=6, fieldbackground="white")
        style.configure("TCombobox", padding=5)
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", padding=(17,10), background="#e8edf5")
        style.map("TNotebook.Tab", background=[("selected", "white")], foreground=[("selected", BLUE)])
        style.configure("Treeview", rowheight=34, background="white", fieldbackground="white", borderwidth=0)
        style.configure("Treeview.Heading", padding=(8,8), background="#e8edf5", font=("Microsoft YaHei UI", 10, "bold"))
        style.map("Treeview", background=[("selected", "#dceaff")], foreground=[("selected", INK)])

    def button(self, master, text, command, *, primary=False, mutation=False, **kwargs):
        button = ttk.Button(master, text=text, command=command, style="Accent.TButton" if primary else "TButton", **kwargs)
        if mutation:
            self.mutating.append(button)
        return button

    def _header(self):
        header = ttk.Frame(self.root, style="Header.TFrame", padding=(24,16))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="MixCut", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        self.header_subtitle = tk.StringVar(value="本地电商视频流水线")
        ttk.Label(header, textvariable=self.header_subtitle, style="Subtitle.TLabel").grid(row=1, column=0, sticky="w", pady=(3,0))
        self.button(header, "刷新", self.refresh).grid(row=0, column=1, padx=5)
        self.button(header, "打开商品目录", lambda: self.open_path(self.service.project.path)).grid(row=0, column=2, padx=5)
        self.button(header, "使用说明", lambda: self.open_path(ROOT / "README.md")).grid(row=0, column=3, padx=(5,0))

    def _stats(self):
        bar = ttk.Frame(self.root, padding=(18,12))
        bar.grid(row=1, column=0, sticky="ew")
        self.stats = {}
        for index, (name,label) in enumerate([("pending", "待审核素材"), ("approved", "已通过素材"), ("clips", "可用片段"), ("outputs", "已出片")]):
            bar.columnconfigure(index, weight=1, uniform="stats")
            card = ttk.Frame(bar, padding=(16,9), style="Stat.TFrame")
            card.grid(row=0, column=index, sticky="ew", padx=(0,10))
            value = tk.StringVar(value="0")
            ttk.Label(card, textvariable=value, style="Number.TLabel").pack(anchor="w")
            ttk.Label(card, text=label, style="Stat.TLabel").pack(anchor="w")
            self.stats[name] = value
        self.ai_badge = tk.StringVar(value="AI 待配置")
        ai = ttk.Frame(bar)
        ai.grid(row=0, column=4, sticky="e")
        self.button(ai, "AI 设置", lambda: self.tabs.select(self.ai_tab)).pack(anchor="e")
        ttk.Label(ai, textvariable=self.ai_badge, style="Muted.TLabel").pack(anchor="e", pady=(6,0))

    def _dashboard(self):
        self.dashboard = ScrollPage(self.tabs)
        self.tabs.add(self.dashboard, text="① 工作台")
        body = self.dashboard.body
        body.columnconfigure(0, weight=1)
        basics = ttk.LabelFrame(body, text="当前商品", padding=14)
        basics.grid(row=0, column=0, sticky="ew")
        basics.columnconfigure(1, weight=1)
        self.keyword = tk.StringVar()
        self.limit = tk.StringVar()
        self.count = tk.StringVar()
        ttk.Label(basics, text="商品关键词").grid(row=0, column=0, padx=(0,12))
        entry = ttk.Entry(basics, textvariable=self.keyword)
        entry.grid(row=0, column=1, sticky="ew", padx=(0,16))
        self.forms.append(entry)
        ttk.Label(basics, text="采集数量").grid(row=0, column=2)
        limit = ttk.Spinbox(basics, from_=1, to=100, width=5, textvariable=self.limit)
        limit.grid(row=0, column=3, padx=(8,16)); self.forms.append(limit)
        self.button(basics, "保存商品", self.save_basic, mutation=True).grid(row=0, column=4)
        ttk.Label(basics, text="更换商品关键词时，会使用独立目录并保留原素材。", style="Muted.TLabel").grid(row=1, column=1, sticky="w", pady=(8,0), columnspan=4)
        flow = ttk.Frame(body)
        flow.grid(row=1, column=0, sticky="ew", pady=16)
        for column in range(3):
            flow.columnconfigure(column, weight=1, uniform="flow")
        collect = ttk.LabelFrame(flow, text="1  采集素材", padding=16)
        collect.grid(row=0, column=0, sticky="nsew", padx=(0,10))
        ttk.Label(collect, text="按商品关键词搜索抖音，\n下载到本地待审核目录。", wraplength=270).pack(anchor="w")
        self.button(collect, "开始抖音采集", self.collect, primary=True, mutation=True).pack(fill="x", pady=(16,8))
        self.button(collect, "查看待审核素材", lambda: self.tabs.select(self.material_tab)).pack(fill="x")
        ttk.Label(collect, text="首次采集请在专用 Chrome 扫码登录，\n并保持浏览器开启。", style="Muted.TLabel").pack(anchor="w", pady=(12,0))
        analyze = ttk.LabelFrame(flow, text="2  审核与分类", padding=16)
        analyze.grid(row=0, column=1, sticky="nsew", padx=5)
        ttk.Label(analyze, text="先保留可用原片，再让 AI\n拆分、识别用途并分类。", wraplength=270).pack(anchor="w")
        self.button(analyze, "AI 拆分分类", self.analyze, primary=True, mutation=True).pack(fill="x", pady=(16,8))
        self.button(analyze, "查看 / 手工切片", lambda: self.tabs.select(self.clip_tab)).pack(fill="x")
        ttk.Label(analyze, text="AI 未配置也能手工切片；\n不确定的片段进入“待确认”。", style="Muted.TLabel").pack(anchor="w", pady=(12,0))
        produce = ttk.LabelFrame(flow, text="3  批量混剪出片", padding=16)
        produce.grid(row=0, column=2, sticky="nsew", padx=(10,0))
        ttk.Label(produce, text="开头 → 卖点 → 效果 → 购买引导\n再追加长尾商品图、音乐和特效。", wraplength=280).pack(anchor="w")
        amount = ttk.Frame(produce); amount.pack(fill="x", pady=(12,8))
        ttk.Label(amount, text="生成数量").pack(side="left")
        spin = ttk.Spinbox(amount, from_=1, to=1000, width=6, textvariable=self.count)
        spin.pack(side="right"); self.forms.append(spin)
        self.button(produce, "生成计划并出片", self.batch, primary=True, mutation=True).pack(fill="x", pady=(0,8))
        self.button(produce, "继续已有计划", lambda: self.start_job("渲染 / 续跑", [["render"]]), mutation=True).pack(fill="x")
        self.readiness = tk.StringVar()
        ready = ttk.LabelFrame(body, text="开始前检查", padding=14)
        ready.grid(row=2, column=0, sticky="ew")
        ttk.Label(ready, textvariable=self.readiness, wraplength=1000, justify="left").pack(anchor="w")
        actions = ttk.Frame(ready); actions.pack(fill="x", pady=(12,0))
        self.button(actions, "选择图片与音乐", lambda: self.tabs.select(self.pack_tab)).pack(side="left", padx=(0,8))
        self.button(actions, "环境检查", lambda: self.start_job("环境检查", [["doctor"]]), mutation=True).pack(side="left", padx=8)
        self.button(actions, "查看测试样片", self.demo, mutation=True).pack(side="right")

    def tree(self, master, columns):
        frame = ttk.Frame(master)
        frame.columnconfigure(0, weight=1); frame.rowconfigure(0, weight=1)
        tree = ttk.Treeview(frame, columns=[x[0] for x in columns], show="headings", selectmode="extended", height=8)
        for key, title, width in columns:
            tree.heading(key, text=title)
            tree.column(key, width=width, minwidth=60, stretch=key in {"title", "name"})
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.grid(row=0, column=0, sticky="nsew"); scrollbar.grid(row=0, column=1, sticky="ns")
        return frame, tree

    def preview(self, master, name):
        box = ttk.LabelFrame(master, text="预览", padding=12)
        box.columnconfigure(0, weight=1)
        box.rowconfigure(1, weight=1)
        self.button(box, "播放所选视频", lambda: self.play_selected(name)).grid(row=0, column=0, sticky="ew", pady=(0,8))
        image = tk.Label(box, text="选择一条素材查看缩略图", bg="#e6edf6", fg=MUTED, width=34, height=7)
        image.grid(row=1, column=0, sticky="nsew")
        info = tk.StringVar(value="双击素材可使用默认播放器查看。")
        ttk.Label(box, textvariable=info, wraplength=270, justify="left").grid(row=2, column=0, sticky="ew", pady=(8,0))
        self.previews = getattr(self, "previews", {})
        self.previews[name] = (image, info)
        return box

    def _materials(self):
        self.material_tab = ttk.Frame(self.tabs, padding=16)
        self.tabs.add(self.material_tab, text="② 素材审核")
        page = self.material_tab
        page.columnconfigure(0, weight=1); page.rowconfigure(1, weight=1)
        toolbar = ttk.Frame(page); toolbar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0,12))
        self.material_filter = tk.StringVar(value="待审核")
        combo = ttk.Combobox(toolbar, textvariable=self.material_filter, values=["待审核", "已通过", "已淘汰"], state="readonly", width=10)
        combo.pack(side="left"); combo.bind("<<ComboboxSelected>>", lambda event: self.refresh_materials())
        self.button(toolbar, "导入本地视频", self.import_materials, mutation=True).pack(side="left", padx=8)
        self.button(toolbar, "保留所选", lambda: self.move_materials("已通过"), primary=True, mutation=True).pack(side="left", padx=4)
        self.button(toolbar, "淘汰所选", lambda: self.move_materials("已淘汰"), mutation=True).pack(side="left", padx=4)
        self.button(toolbar, "退回待审核", lambda: self.move_materials("待审核"), mutation=True).pack(side="left", padx=4)
        self.button(toolbar, "打开文件夹", self.open_material_folder).pack(side="right")
        frame, self.material_tree = self.tree(page, [("title", "素材 / 作品", 270), ("duration", "时长", 85), ("size", "大小", 85)])
        frame.grid(row=1, column=0, sticky="nsew", padx=(0,14))
        self.preview(page, "materials").grid(row=1, column=1, sticky="ns")
        self.material_tree.bind("<<TreeviewSelect>>", lambda event: self.selection_preview("materials"))
        self.material_tree.bind("<Double-1>", lambda event: self.play_selected("materials"))
        ttk.Label(page, text="“淘汰”只移入当前商品的已淘汰目录，之后可以恢复；不会删除原片。", style="Muted.TLabel").grid(row=2, column=0, columnspan=2, sticky="w", pady=(10,0))

    def _clips(self):
        self.clip_tab = ttk.Frame(self.tabs, padding=16)
        self.tabs.add(self.clip_tab, text="③ 片段分类")
        page = self.clip_tab
        page.columnconfigure(0, weight=1); page.rowconfigure(1, weight=1)
        toolbar = ttk.Frame(page); toolbar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0,12))
        self.clip_filter = tk.StringVar(value="全部分类")
        combo = ttk.Combobox(toolbar, textvariable=self.clip_filter, values=["全部分类", *ROLES.values(), "待确认"], state="readonly", width=12)
        combo.pack(side="left"); combo.bind("<<ComboboxSelected>>", lambda event: self.refresh_clips())
        ttk.Label(toolbar, text="将所选移到").pack(side="left", padx=(16,6))
        self.clip_target = tk.StringVar(value="开头")
        ttk.Combobox(toolbar, textvariable=self.clip_target, values=[*ROLES.values(), "待确认"], state="readonly", width=11).pack(side="left")
        self.button(toolbar, "确认分类", self.move_clips, mutation=True).pack(side="left", padx=8)
        self.button(toolbar, "AI 拆分分类", self.analyze, primary=True, mutation=True).pack(side="right")
        frame, self.clip_tree = self.tree(page, [("category", "类别", 90), ("name", "片段", 240), ("duration", "时长", 80)])
        frame.grid(row=1, column=0, sticky="nsew", padx=(0,14))
        self.preview(page, "clips").grid(row=1, column=1, sticky="ns")
        self.clip_tree.bind("<<TreeviewSelect>>", lambda event: self.selection_preview("clips"))
        self.clip_tree.bind("<Double-1>", lambda event: self.play_selected("clips"))
        manual = ttk.LabelFrame(page, text="手工切片 · AI 尚未配置时也可使用", padding=10)
        manual.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(14,0))
        manual.columnconfigure(1, weight=1)
        self.manual_source = tk.StringVar()
        self.manual_start = tk.StringVar(value="0")
        self.manual_end = tk.StringVar(value="5")
        self.manual_role = tk.StringVar(value="开头")
        ttk.Label(manual, text="已通过原片").grid(row=0, column=0, padx=(0,8))
        self.manual_combo = ttk.Combobox(manual, textvariable=self.manual_source, state="readonly")
        self.manual_combo.grid(row=0, column=1, sticky="ew", padx=(0,10))
        ttk.Label(manual, text="开始 / 秒").grid(row=0, column=2)
        entry = ttk.Entry(manual, textvariable=self.manual_start, width=6); entry.grid(row=0, column=3, padx=6); self.forms.append(entry)
        ttk.Label(manual, text="结束 / 秒").grid(row=0, column=4)
        entry = ttk.Entry(manual, textvariable=self.manual_end, width=6); entry.grid(row=0, column=5, padx=6); self.forms.append(entry)
        ttk.Combobox(manual, textvariable=self.manual_role, values=list(ROLES.values()), state="readonly", width=10).grid(row=0, column=6, padx=6)
        self.button(manual, "切出片段", self.manual_clip, mutation=True).grid(row=0, column=7, padx=(6,0))

    def path_field(self, master, row, label, variable, filetypes):
        ttk.Label(master, text=label).grid(row=row, column=0, sticky="w", pady=7, padx=(0,12))
        entry = ttk.Entry(master, textvariable=variable)
        entry.grid(row=row, column=1, sticky="ew", pady=7)
        self.forms.append(entry)
        self.button(master, "选择文件", lambda: self.choose_file(variable, filetypes), mutation=True).grid(row=row, column=2, padx=(10,0), pady=7)

    def _packaging(self):
        self.pack_tab = ScrollPage(self.tabs)
        self.tabs.add(self.pack_tab, text="④ 包装出片")
        page = self.pack_tab.body; page.columnconfigure(0, weight=1)
        assets = ttk.LabelFrame(page, text="每条视频的图片、音乐与特效", padding=14)
        assets.grid(row=0, column=0, sticky="ew"); assets.columnconfigure(1, weight=1)
        self.image_path = tk.StringVar(); self.music_path = tk.StringVar(); self.effect_path = tk.StringVar()
        self.tail = tk.StringVar(); self.resolution = tk.StringVar(); self.fps = tk.StringVar()
        self.music_volume = tk.DoubleVar(); self.original_volume = tk.DoubleVar(); self.intensity = tk.DoubleVar()
        self.meteor = tk.BooleanVar(); self.scope = tk.StringVar()
        self.path_field(assets, 0, "末尾商品图", self.image_path, [("图片", "*.png *.jpg *.jpeg *.webp *.bmp"), ("全部文件", "*.*")])
        self.path_field(assets, 1, "背景音乐", self.music_path, [("音频", "*.mp3 *.wav *.m4a *.flac *.ogg"), ("全部文件", "*.*")])
        options = ttk.Frame(assets); options.grid(row=2, column=0, columnspan=3, sticky="ew", pady=8)
        for label, variable, width, choices in [("尾图秒数",self.tail,7,None),("画幅",self.resolution,17,["720 × 1280","1080 × 1920","540 × 960"]),("帧率",self.fps,6,["24","25","30"]),("出片数",self.count,6,None)]:
            ttk.Label(options, text=label).pack(side="left", padx=(0,6))
            widget = ttk.Combobox(options, textvariable=variable, values=choices, width=width, state="readonly") if choices else ttk.Spinbox(options, textvariable=variable, from_=1, to=3600 if variable is self.tail else 1000, width=width)
            widget.pack(side="left", padx=(0,16)); self.forms.append(widget)
            if variable is self.resolution: self.resolution_combo = widget
            if variable is self.fps: self.fps_combo = widget
        sound = ttk.Frame(assets); sound.grid(row=3, column=0, columnspan=3, sticky="ew", pady=6)
        self.slider(sound, "音乐音量", self.music_volume).pack(side="left", fill="x", expand=True, padx=(0,18))
        self.slider(sound, "原声音量", self.original_volume).pack(side="left", fill="x", expand=True)
        fx = ttk.Frame(assets); fx.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(8,2))
        checkbox = ttk.Checkbutton(fx, text="启用流星雨 / 氛围效果", variable=self.meteor)
        checkbox.pack(side="left"); self.mutating.append(checkbox)
        ttk.Label(fx, text="作用范围").pack(side="left", padx=(20,6))
        ttk.Combobox(fx, textvariable=self.scope, values=list(SCOPE_LABELS), state="readonly", width=12).pack(side="left")
        self.slider(fx, "强度", self.intensity, length=140).pack(side="right")
        self.path_field(assets, 5, "自定义动效（可选）", self.effect_path, [("透明动效", "*.mov *.webm"), ("视频", "*.mp4 *.mov *.webm")])
        ttk.Label(assets, text="留空使用内置原创流星动效；自定义文件应带透明通道。默认在末尾追加 300 秒商品图。", style="Muted.TLabel").grid(row=6, column=0, columnspan=3, sticky="w", pady=(5,10))
        save = ttk.Frame(assets); save.grid(row=7, column=0, columnspan=3, sticky="ew")
        self.button(save, "保存包装设置", self.save_render, primary=True, mutation=True).pack(side="left")
        self.button(save, "应用到待完成计划", self.apply_pending, mutation=True).pack(side="left", padx=10)
        ttk.Label(save, text="已完成的计划不会改动。", style="Muted.TLabel").pack(side="left")
        plans = ttk.LabelFrame(page, text="组合计划与出片", padding=12)
        plans.grid(row=1, column=0, sticky="ew", pady=(16,0)); plans.columnconfigure(0, weight=1)
        frame, self.plan_tree = self.tree(plans, [("id", "计划编号", 200), ("body", "主体", 90), ("tail", "尾图", 90), ("status", "状态", 100)])
        frame.grid(row=0, column=0, sticky="ew"); self.plan_tree.configure(height=4)
        actions = ttk.Frame(plans); actions.grid(row=1, column=0, sticky="ew", pady=(10,0))
        self.button(actions, "只生成计划", self.plan, mutation=True).pack(side="left")
        self.button(actions, "渲染 / 续跑", lambda: self.start_job("渲染 / 续跑", [["render"]]), primary=True, mutation=True).pack(side="left", padx=8)
        self.button(actions, "导出所选剪映草稿", self.draft, mutation=True).pack(side="left")
        self.button(actions, "打开成片目录", lambda: self.open_path(self.service.project.outputs)).pack(side="right")
        self.plan_tree.bind("<Double-1>", lambda event: self.open_plan_output())
        ttk.Label(plans, text="双击已完成计划可播放成片。剪映草稿需在剪映中检查、导出。", style="Muted.TLabel").grid(row=2, column=0, sticky="w", pady=(9,0))

    def slider(self, master, label, variable, length=200):
        box = ttk.Frame(master)
        ttk.Label(box, text=label).pack(side="left", padx=(0,8))
        slider = ttk.Scale(box, variable=variable, from_=0, to=1, length=length)
        slider.pack(side="left", fill="x", expand=True); self.mutating.append(slider)
        value = tk.StringVar()
        def update(*args): value.set(f"{variable.get()*100:.0f}%")
        variable.trace_add("write", update); update()
        ttk.Label(box, textvariable=value, width=5, anchor="e").pack(side="left", padx=(6,0))
        return box

    def _ai(self):
        self.ai_tab = ScrollPage(self.tabs)
        self.tabs.add(self.ai_tab, text="⑤ AI 设置")
        page = self.ai_tab.body; page.columnconfigure(0, weight=1)
        self.ai_url = tk.StringVar(); self.ai_model = tk.StringVar(); self.ai_key = tk.StringVar()
        self.asr_url = tk.StringVar(); self.asr_model = tk.StringVar(); self.asr_key = tk.StringVar()
        self.key_status = tk.StringVar()
        for row, title, fields in [(0,"视觉分类服务",[("服务地址",self.ai_url,False),("模型名称",self.ai_model,False),("API 密钥",self.ai_key,True)]),(1,"语音转写服务（可选）",[("服务地址",self.asr_url,False),("模型名称",self.asr_model,False),("API 密钥",self.asr_key,True)])]:
            box = ttk.LabelFrame(page, text=title, padding=16)
            box.grid(row=row, column=0, sticky="ew", pady=(0,14)); box.columnconfigure(1, weight=1)
            for index, (label, variable, secret) in enumerate(fields):
                ttk.Label(box, text=label).grid(row=index, column=0, sticky="w", padx=(0,18), pady=7)
                entry = ttk.Entry(box, textvariable=variable, show="●" if secret else "")
                entry.grid(row=index, column=1, sticky="ew", pady=7); self.forms.append(entry)
            text = "填写支持图片理解的兼容服务，例如 https://你的服务/v1；密钥留空会保留已保存值。" if row == 0 else "支持兼容音频转写服务，也可提供原视频同名 SRT。缺少台词时间戳时，切片先进入“待确认”。"
            ttk.Label(box, text=text, style="Muted.TLabel", wraplength=940).grid(row=3, column=0, columnspan=2, sticky="w", pady=(8,0))
        controls = ttk.Frame(page); controls.grid(row=2, column=0, sticky="ew")
        self.button(controls, "保存 AI 设置", self.save_ai, primary=True, mutation=True).pack(side="left")
        ttk.Label(controls, textvariable=self.key_status, style="Muted.TLabel").pack(side="left", padx=14)
        ttk.Label(page, text="保存设置不会调用模型。密钥仅保存在本机，不会显示在运行日志中。", style="Muted.TLabel").grid(row=3, column=0, sticky="w", pady=(12,0))

    def _logs(self):
        frame = ttk.LabelFrame(self.panes, text="运行日志", padding=(10,5))
        self.panes.add(frame, weight=1)
        frame.columnconfigure(0, weight=1); frame.rowconfigure(1, weight=1)
        toolbar = ttk.Frame(frame); toolbar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0,4))
        self.task_label = tk.StringVar(value="当前没有运行任务")
        ttk.Label(toolbar, textvariable=self.task_label, style="Muted.TLabel").pack(side="left")
        self.stop_button = ttk.Button(toolbar, text="停止当前任务", command=self.stop, style="Danger.TButton", state="disabled")
        self.stop_button.pack(side="right")
        self.button(toolbar, "清空日志", self.clear_log).pack(side="right", padx=8)
        self.progress = ttk.Progressbar(toolbar, mode="indeterminate", length=180)
        self.progress.pack(side="right", padx=12)
        self.log = tk.Text(frame, height=6, wrap="word", font=("Consolas", 10), bg="#111f33", fg="#d7e3f3", insertbackground="white", relief="flat", padx=10, pady=8, state="disabled")
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=scrollbar.set)
        self.log.grid(row=1, column=0, sticky="nsew"); scrollbar.grid(row=1, column=1, sticky="ns")

    def load_fields(self):
        config = self.service.project.config
        self.keyword.set(config["product"]["keyword"])
        self.limit.set(str(config["collection"]["limit"]))
        self.count.set(str(config["mix"]["count"]))
        render = config["render"]
        for variable, field in [(self.image_path,"product_image"),(self.music_path,"music"),(self.effect_path,"effect_video")]: variable.set(render.get(field,""))
        self.tail.set(str(render["tail_seconds"]))
        current = f"{render['width']} × {render['height']}"
        self.resolution_combo.configure(values=list(dict.fromkeys([current,"720 × 1280","1080 × 1920","540 × 960"])))
        self.resolution.set(current)
        self.fps_combo.configure(values=list(dict.fromkeys([str(render["fps"]),"24","25","30"])))
        self.fps.set(str(render["fps"]))
        self.music_volume.set(render.get("music_volume",.18)); self.original_volume.set(render.get("original_audio_volume",.7))
        effect = render.get("meteor",{})
        self.meteor.set(effect.get("enabled",True)); self.intensity.set(effect.get("intensity",.55))
        self.scope.set(next((label for label,value in SCOPE_LABELS.items() if value == effect.get("scope","all")),"全片"))
        ai = config["analysis"]; asr = ai.get("transcription",{})
        self.ai_url.set(ai.get("base_url","")); self.ai_model.set(ai.get("model",""))
        self.asr_url.set(asr.get("base_url","")); self.asr_model.set(asr.get("model","whisper-1"))
        self.ai_key.set(""); self.asr_key.set("")
        self.key_status.set("已有密钥保留，留空不修改" if self.service.secret_values() else "尚未保存密钥，本地免密服务可留空")

    def alert(self, error):
        messagebox.showerror("操作未完成", str(error), parent=self.root)
        self.append_log(str(error))

    def _guard(self):
        if self.runner.busy:
            raise PipelineError("任务运行中，请先等待或停止当前任务。")

    def save_basic(self):
        try:
            self._guard()
            changed = self.keyword.get().strip() != self.service.project.keyword
            if changed and not messagebox.askyesno("切换商品", "新商品将使用独立素材目录，商品图和音乐需要重新选择。原商品文件会保留。继续吗？", parent=self.root):
                return False
            self.service.save_basic(self.keyword.get(), self.limit.get(), self.count.get())
            if changed: self.load_fields()
            self.refresh(); self.note("商品与数量设置已保存")
            return True
        except (PipelineError, OSError, ValueError) as error:
            self.alert(error); return False

    def render_values(self):
        width, height = self.resolution.get().split("×")
        return {"width":width.strip(),"height":height.strip(),"fps":self.fps.get(),"tail_seconds":self.tail.get(),"product_image":self.image_path.get(),"music":self.music_path.get(),"effect_video":self.effect_path.get(),"music_volume":self.music_volume.get(),"original_audio_volume":self.original_volume.get(),"meteor_enabled":self.meteor.get(),"scope":SCOPE_LABELS[self.scope.get()],"intensity":self.intensity.get()}

    def save_render(self):
        try:
            self._guard(); self.service.save_render(self.render_values()); self.refresh(); self.note("包装设置已保存，新计划将使用这些设置")
            return True
        except (PipelineError, OSError, ValueError, KeyError) as error:
            self.alert(error); return False

    def save_ai(self):
        try:
            self._guard()
            self.service.save_ai(base_url=self.ai_url.get(),model=self.ai_model.get(),api_key=self.ai_key.get(),asr_url=self.asr_url.get(),asr_model=self.asr_model.get(),asr_key=self.asr_key.get())
            self.ai_key.set(""); self.asr_key.set("")
            self.key_status.set("设置已保存；密钥输入框留空时保留原值")
            self.refresh(); self.note("AI 设置已保存，尚未发起模型请求")
        except (PipelineError,OSError,ValueError) as error: self.alert(error)

    def collect(self):
        if self.save_basic(): self.start_job("抖音采集", [["collect","--limit",self.limit.get()]])

    def analyze(self):
        if not self.service.project.config["analysis"].get("base_url") or not self.service.project.config["analysis"].get("model"):
            self.tabs.select(self.ai_tab)
            messagebox.showinfo("先配置 AI 服务", "请填写视觉服务地址和模型名称。也可以在“片段分类”中手工切片。", parent=self.root)
            return
        if not self.service.project.approved_videos():
            self.tabs.select(self.material_tab)
            messagebox.showinfo("先审核原片", "请在素材审核页选择可用视频，点击“保留所选”。", parent=self.root)
            return
        self.start_job("AI 拆分分类", [["analyze"]])

    def plan(self):
        if self.save_basic() and self.save_render(): self.start_job("生成组合计划", [["plan","--count",self.count.get()]])

    def batch(self):
        if not self.save_basic() or not self.save_render(): return
        cfg = self.service.project.config["render"]
        if not cfg.get("product_image") or not cfg.get("music"):
            self.tabs.select(self.pack_tab)
            messagebox.showinfo("补齐出片素材", "请先选择商品图片和背景音乐，再开始批量出片。", parent=self.root)
            return
        self.start_job("批量出片", [["plan","--count",self.count.get()],["render"]])

    def apply_pending(self):
        if self.save_render():
            try:
                count = self.service.apply_render_to_pending(); self.refresh(); self.note(f"已更新 {count} 个待完成计划的包装设置")
            except (PipelineError,OSError,ValueError) as error: self.alert(error)

    def import_materials(self):
        paths = filedialog.askopenfilenames(parent=self.root,title="导入待审核视频",filetypes=[("视频","*.mp4 *.mov *.mkv *.webm *.avi")])
        if paths: self.start_job("导入本地素材", [["import",*paths]])

    def selected(self, name):
        tree = self.material_tree if name == "materials" else self.clip_tree
        return [self.row_maps.get(name,{}).get(item) for item in tree.selection() if item in self.row_maps.get(name,{})]

    def move_materials(self, target):
        try:
            self._guard()
            count = self.service.move_materials([x["path"] for x in self.selected("materials")],target)
            self.refresh(); self.note(f"{count} 条素材已移到“{target}”")
        except (PipelineError,OSError,ValueError) as error: self.alert(error)

    def move_clips(self):
        try:
            self._guard()
            count = self.service.move_clips([x["path"] for x in self.selected("clips")], self.clip_target.get())
            self.refresh(); self.note(f"已确认 {count} 条片段的分类")
        except (PipelineError,OSError,ValueError) as error: self.alert(error)

    def manual_clip(self):
        sources = {p.name:p for p in self.service.project.approved_videos()}
        source = sources.get(self.manual_source.get())
        if not source:
            self.alert("请先审核原片，再从下拉框选择一条。"); return
        try:
            start, end = float(self.manual_start.get()), float(self.manual_end.get())
            if not 0 <= start < end: raise ValueError()
        except ValueError:
            self.alert("开始与结束需填写秒数，且结束大于开始。"); return
        role = next(key for key,value in ROLES.items() if value == self.manual_role.get())
        self.start_job("手工切片", [["clip",str(source),"--start",str(start),"--end",str(end),"--role",role]])

    def draft(self):
        selection = self.plan_tree.selection()
        if len(selection) != 1:
            self.alert("请在计划列表中选择一条计划。"); return
        self.start_job("导出剪映草稿", [["draft",selection[0]]])

    def demo(self):
        report = read_json(ROOT / "examples/demo_720x1280_300s/demo_report.json",{})
        reports = report.get("reports",[])
        if reports and Path(reports[0]["path"]).is_file():
            self.open_path(Path(reports[0]["path"]))
        else:
            self.start_job("合成素材测试出片", [["demo"]])

    def choose_file(self, variable, filetypes):
        path = filedialog.askopenfilename(parent=self.root,title="选择本地文件",filetypes=filetypes)
        if path:
            resolved = Path(path).resolve()
            try: value = resolved.relative_to(self.service.project.root).as_posix()
            except ValueError: value = str(resolved)
            variable.set(value)

    def open_path(self, path):
        path = Path(path)
        if not path.exists(): self.alert("文件或目录不存在："+str(path)); return
        try: os.startfile(str(path.resolve()))
        except OSError as error: self.alert(error)

    def open_material_folder(self):
        folder = {"待审核":self.service.project.inbox,"已通过":self.service.project.approved,"已淘汰":self.service.rejected}[self.material_filter.get()]
        folder.mkdir(parents=True,exist_ok=True); self.open_path(folder)

    def play_selected(self, name):
        rows = self.selected(name)
        if rows: self.open_path(rows[0]["path"])

    def open_plan_output(self):
        selection = self.plan_tree.selection()
        if selection:
            row = self.row_maps.get("plans",{}).get(selection[0],{})
            if row.get("output"): self.open_path(Path(row["output"]))

    def refresh(self):
        try:
            status = self.service.status()
            self.header_subtitle.set(f"本地电商视频流水线  /  {status['product']}")
            for key,value in {"pending":status["pending_review"],"approved":status["approved_videos"],"clips":sum(value for label,value in status["clips"].items() if label != "待确认"),"outputs":status["outputs"]}.items(): self.stats[key].set(str(value))
            self.ai_badge.set("AI 已配置" if status["ai_configured"] else "AI 待配置")
            cfg = self.service.project.config["render"]
            checks = ["AI 服务："+("已配置" if status["ai_configured"] else "待配置，可先手工切片"),"商品图："+("已选择" if cfg.get("product_image") else "待选择"),"音乐："+("已选择" if cfg.get("music") else "待选择"),f"尾图：{float(cfg['tail_seconds']):g} 秒"]
            self.readiness.set("    ·    ".join(checks))
            self.refresh_materials(); self.refresh_clips(); self.refresh_plans()
            names = [p.name for p in self.service.project.approved_videos()]
            self.manual_combo.configure(values=names)
            if self.manual_source.get() not in names: self.manual_source.set(names[0] if names else "")
        except (PipelineError,OSError,ValueError) as error: self.alert(error)

    def populate(self, tree, name, rows, values):
        selected_paths = {str(x["path"]) for x in self.selected(name)} if name in {"materials","clips"} else set()
        tree.delete(*tree.get_children())
        mapping = {}
        for index,row in enumerate(rows):
            iid = str(index)
            tree.insert("","end",iid=iid,values=values(row)); mapping[iid]=row
        self.row_maps[name]=mapping
        selected = [iid for iid,row in mapping.items() if str(row["path"]) in selected_paths]
        if selected: tree.selection_set(selected)
        else:
            self.preview_tokens.pop(name, None)
            self.photos.pop(name, None)
            self.previews[name][0].configure(image="", text="选择一条素材查看缩略图", width=34, height=7)
            self.previews[name][1].set("双击素材可使用默认播放器查看。")

    def refresh_materials(self):
        self.populate(self.material_tree,"materials",self.service.material_rows(self.material_filter.get()),lambda r:(r["title"],duration_text(r["duration"]),f"{r['size']/1024/1024:.1f} MB"))

    def refresh_clips(self):
        self.populate(self.clip_tree,"clips",self.service.clip_rows(self.clip_filter.get()),lambda r:(r["category"],r["name"],duration_text(r["duration"])))

    def refresh_plans(self):
        selected=self.plan_tree.selection(); self.plan_tree.delete(*self.plan_tree.get_children())
        mapping={}
        labels={"pending":"待开始","running":"运行中","complete":"MP4 已完成","draft_ready":"草稿已生成","failed":"待重试"}
        for row in self.service.plan_rows():
            self.plan_tree.insert("","end",iid=row["id"],values=(row["id"],duration_text(row["body_seconds"]),duration_text(row["tail_seconds"]),labels.get(row["status"],row["status"])))
            mapping[row["id"]]=row
        self.row_maps["plans"]=mapping
        if selected and selected[0] in mapping: self.plan_tree.selection_set(selected[0])

    def selection_preview(self, name):
        rows=self.selected(name)
        if not rows: return
        row=rows[0]; path=row["path"]
        self.previews[name][1].set(preview_text(row,row.get('duration')))
        token=object_hash({"path":str(path),"mtime":path.stat().st_mtime_ns,"size":[280,110]})
        self.preview_tokens[name]=token
        def work():
            try:
                cache=self.service.project.state/"gui-thumbnails"; cache.mkdir(exist_ok=True)
                target=cache/(token+".png")
                if not target.exists():
                    flags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0
                    result=subprocess.run([runtime_tool("ffmpeg"),"-v","error","-nostdin","-y","-ss","0","-i",str(path),"-frames:v","1","-vf","scale=280:110:force_original_aspect_ratio=decrease,pad=280:110:(ow-iw)/2:(oh-ih)/2:color=0xe6edf6",str(target)],capture_output=True,timeout=35,creationflags=flags)
                    if result.returncode: raise PipelineError("缩略图暂不可用")
                info=media_info(path)
                seconds=float(info.get("format",{}).get("duration",0))
                self.events.put(("preview",(name,token,target,seconds,row)))
            except Exception:
                self.events.put(("preview_error",(name,token)))
        threading.Thread(target=work,daemon=True).start()

    def start_job(self, label, commands):
        try:
            self._guard()
            self.runner.config_path=self.service.config_path
            self.runner.secrets=self.service.secret_values()
            self.runner.start(label,commands)
            self._busy(True); self.progress.start(12)
            self.task_label.set(label+" · 正在运行")
            self.note("开始："+label)
        except (PipelineError,OSError,ValueError) as error: self.alert(error)

    def _busy(self, busy):
        for widget in [*self.mutating,*self.forms]:
            try:
                if busy: widget.state(["disabled"])
                else: widget.state(["!disabled"])
            except tk.TclError: pass
        self.stop_button.configure(state="normal" if busy else "disabled")

    def stop(self):
        if self.runner.busy:
            self.runner.stop(); self.task_label.set("正在停止当前任务…"); self.stop_button.configure(state="disabled")

    def poll(self):
        if self.closing: return
        for source in [self.runner.events,self.events]:
            while True:
                try: kind,value=source.get_nowait()
                except queue.Empty: break
                if kind=="log": self.append_log(value)
                elif kind=="step": self.task_label.set(value)
                elif kind=="done":
                    self.progress.stop(); self._busy(False)
                    status="已停止" if value["cancelled"] else "已完成" if value["code"]==0 else "未完成，请查看日志"
                    self.task_label.set(value["label"]+" · "+status); self.note(value["label"]+"："+status)
                    self.refresh()
                elif kind=="preview":
                    name,token,target,seconds,row=value
                    if self.preview_tokens.get(name)==token:
                        try:
                            photo=tk.PhotoImage(file=str(target)); self.photos[name]=photo
                            self.previews[name][0].configure(image=photo,text="",width=280,height=110)
                            self.previews[name][1].set(preview_text(row,seconds))
                        except tk.TclError: pass
                elif kind=="preview_error":
                    name,token=value
                    if self.preview_tokens.get(name)==token:
                        self.previews[name][0].configure(image="",text="缩略图暂不可用，仍可播放原片")
        self.root.after(100,self.poll)

    def append_log(self, text):
        for secret in self.service.secret_values(): text=text.replace(secret,"[已隐藏密钥]")
        self.log.configure(state="normal"); self.log.insert("end",text+"\n")
        if int(self.log.index("end-1c").split(".")[0])>2500: self.log.delete("1.0","500.0")
        self.log.see("end"); self.log.configure(state="disabled")

    def note(self, text):
        self.status_var.set(text); self.append_log(datetime.now().strftime("%H:%M:%S")+"  "+text)

    def clear_log(self):
        self.log.configure(state="normal"); self.log.delete("1.0","end"); self.log.configure(state="disabled")

    def _wheel(self, event):
        widget=event.widget
        if widget.winfo_class() in {"Text","Treeview","TCombobox","Spinbox","TSpinbox"}: return
        while widget is not None:
            if isinstance(widget,ScrollPage):
                widget.canvas.yview_scroll(int(-event.delta/120),"units"); return "break"
            widget=getattr(widget,"master",None)

    def close(self):
        if self.runner.busy:
            if not messagebox.askyesno("任务还在运行", "关闭窗口会停止当前任务，已完成文件会保留。现在关闭吗？", parent=self.root): return
            self.runner.stop()
            self.task_label.set("正在停止，请稍候…")
            self.root.after(150,self._close_after_stop)
        else:
            self.closing=True; self.root.destroy()

    def _close_after_stop(self):
        if self.runner.busy:
            self.root.after(150,self._close_after_stop)
        else:
            self.closing=True; self.root.destroy()

def launch(config_path: Path):
    if os.name == "nt":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("MixCut.Desktop")
    root=tk.Tk()
    icon=ROOT/"assets/icons/mixcut.ico"
    if icon.is_file():
        try:root.iconbitmap(default=str(icon))
        except tk.TclError:pass
    if read_json(config_path).get("workflow") == "folder_mix":
        from .folder_gui import FolderMixApp
        app=FolderMixApp(root,config_path)
    else:
        app=MixCutApp(root,config_path)
    root.mainloop()
