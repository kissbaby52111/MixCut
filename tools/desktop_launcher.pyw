"""Quiet desktop entry point; resolve the current project on every launch."""
from datetime import datetime
import json
import os
from pathlib import Path
import runpy
import sys
import traceback

ROOT = Path(__file__).resolve().parents[1]


def launch():
    os.chdir(ROOT)
    sys.path[:0] = [str(ROOT), str(ROOT / ".runtime/packages")]
    active = ROOT / ".runtime/active_gui_config.json"
    config = ROOT / "config.json"
    if active.is_file():
        value = json.loads(active.read_text(encoding="utf-8-sig"))
        if not value.get("config_path"):
            raise ValueError("当前项目配置为空，请检查 .runtime/active_gui_config.json。")
        config = (ROOT / value["config_path"]).resolve()
    if not config.is_file():
        raise FileNotFoundError(f"找不到当前项目配置：{config}")
    print(f"{datetime.now().isoformat(timespec='seconds')} 启动 MixCut：{config}", flush=True)
    sys.argv = [str(ROOT / "run.py"), "--config", str(config), "gui"]
    runpy.run_path(str(ROOT / "run.py"), run_name="__main__")


if __name__ == "__main__":
    log_path = ROOT / ".runtime/desktop_launch.log"
    log_path.parent.mkdir(exist_ok=True)
    with log_path.open("a", encoding="utf-8", buffering=1) as log:
        sys.stdout = sys.stderr = log
        try:
            launch()
        except BaseException as error:
            if isinstance(error, SystemExit) and error.code in (None, 0):
                raise
            traceback.print_exc()
            import ctypes
            ctypes.windll.user32.MessageBoxW(None,
                f"MixCut 启动失败：{error}\n\n详细日志：{log_path}", "MixCut", 0x10)
