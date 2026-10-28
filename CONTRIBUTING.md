# 参与开发

开发环境为 Windows 10/11、Python 3.11+（含 Tk）。先运行 `setup.bat`，再执行 `test.bat`；测试生成合成视频与音频，不需要真实商品素材或模型密钥。

测试读取 `config.example.json`，不读取开发者的商品配置。新增回归测试时使用临时目录、合成媒体和模拟 API，不连接付费模型或个人平台账号。

主要模块：

| 模块 | 职责 |
| --- | --- |
| `folder_gui.py`、`folder_service.py` | 文件夹流程的界面与操作 |
| `folder_mix.py`、`planning.py`、`plan_batches.py` | 选材、计划冻结、批次隔离与组合排重 |
| `harvest.py`、`local_music.py` | 本地音乐导入、验证和顺序配乐 |
| `auto_packaging.py`、`corner_overlay.py` | 特效参数和四角图计划 |
| `draft.py`、`compound_draft.py` | 剪映单条与批量复合草稿 |
| `render.py` | FFmpeg MP4 输出 |
| `collect.py`、`analyze.py` | 可选的采集、拆分与分类 |

提交前检查暂存区，勿加入 `.env`、`config.json`、`.runtime`、真实素材、音乐、生成草稿、个人路径或账号数据。公开问题反馈请先对日志脱敏。

修改 vendored SDK 时保留原许可，并同步 `NOTICE` 中的修改说明。原生剪映效果需在客户端核验；结构测试通过不能替代实际播放和导出检查。
