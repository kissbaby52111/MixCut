"""A small offline search dialog for JianYing music/effect metadata."""
from tkinter import ttk
import tkinter as tk
from .jianying_assets import catalog


class AssetPicker:
    def __init__(self, parent, kind):
        self.result = None
        self.kind = kind
        self.items = catalog(kind)
        self.window = tk.Toplevel(parent)
        self.window.title("选择剪映曲库音乐" if kind == "music" else "选择剪映原生特效")
        self.window.geometry("820x540");self.window.minsize(680,420)
        self.window.transient(parent)
        self.window.columnconfigure(0,weight=1);self.window.rowconfigure(1,weight=1)
        search=ttk.Frame(self.window,padding=14);search.grid(row=0,column=0,sticky="ew")
        ttk.Label(search,text="搜索名称、分类或 ID").pack(side="left",padx=(0,12))
        self.query=tk.StringVar();entry=ttk.Entry(search,textvariable=self.query);entry.pack(side="left",fill="x",expand=True)
        frame=ttk.Frame(self.window,padding=(14,0));frame.grid(row=1,column=0,sticky="nsew")
        frame.columnconfigure(0,weight=1);frame.rowconfigure(0,weight=1)
        self.tree=ttk.Treeview(frame,columns=("name","info","id"),show="headings",selectmode="browse")
        for key,title,width in [("name","名称",330),("info","分类 / 时长" if kind=="music" else "素材标记",160),("id","素材 ID",200)]:
            self.tree.heading(key,text=title);self.tree.column(key,width=width,minwidth=70)
        self.tree.grid(row=0,column=0,sticky="nsew")
        bar=ttk.Scrollbar(frame,command=self.tree.yview);bar.grid(row=0,column=1,sticky="ns");self.tree.configure(yscrollcommand=bar.set)
        bottom=ttk.Frame(self.window,padding=14);bottom.grid(row=2,column=0,sticky="ew")
        self.count=tk.StringVar();ttk.Label(bottom,textvariable=self.count).pack(side="left")
        ttk.Button(bottom,text="使用所选",command=self.choose).pack(side="right")
        ttk.Button(bottom,text="取消",command=self.window.destroy).pack(side="right",padx=8)
        ttk.Label(self.window,text="索引仅供选择；音乐/特效由剪映按账号权限加载。",padding=(14,0,14,14)).grid(row=3,column=0,sticky="w")
        self.query.trace_add("write",lambda *args:self.search())
        self.tree.bind("<Double-1>",lambda event:self.choose())
        self.window.bind("<Escape>",lambda event:self.window.destroy())
        self.search();entry.focus_set();self.window.grab_set();parent.wait_window(self.window)

    def search(self):
        terms=self.query.get().lower().split()
        matches=[(index,item) for index,item in enumerate(self.items) if all(term in " ".join(str(value) for value in item.values()).lower() for term in terms)]
        self.tree.delete(*self.tree.get_children())
        for index,item in matches[:300]:
            info=f"{item.get('category','')} · {item['seconds']:g} 秒" if self.kind=="music" else "VIP" if item.get("vip") else "普通"
            self.tree.insert("","end",iid=str(index),values=(item["name"],info,item.get("id",item.get("resource_id",""))))
        self.count.set(f"匹配 {len(matches)} / 共 {len(self.items)} 项，显示前 300 项")

    def choose(self):
        selected=self.tree.selection()
        if selected:
            self.result=dict(self.items[int(selected[0])]);self.window.destroy()
