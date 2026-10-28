# 可选采集与 AI 分类流程

默认 `workflow: "folder_mix"` 直接读取预先分类的视频文件夹，不采集、不调用 AI。现有采集和 AI 模块属于可选流程，平台网页、登录状态和模型服务变化可能影响兼容性。

需要探索时，先复制 `config.example.json` 为单独的本地配置，把 `workflow` 改为 `ai_pipeline` 并设置独立 `workspace`；使用 `run.bat --config 路径 gui` 打开对应五页界面。不要覆盖已有商品配置。

## 组件

- 采集：socai CLI，专用 Chrome 配置保存在 `.runtime/socai-home`。按商品词采集抖音视频到待审目录，需用户在本机完成登录；不保证任意平台或网页版本都能采集。
- 审核：保留允许使用的原片后，才进入拆分和分类。
- 拆分：FFmpeg 场景检测、可选转写及句子边界共同生成候选片段。
- 分类：按片段抽帧调用兼容 OpenAI Chat Completions 的视觉 API，分类为开头、痛点、卖点、效果展示、购买引导、结尾；低置信度结果留待人工确认。
- 后处理：按分类生成组合计划，输出到独立工作目录。此模式不同于文件夹流程的五个自定义段落。

## 安装与 Key

默认安装不下载采集器。需要采集时运行 `setup.bat -WithCollector`；上游固定版本与来源见 `VENDOR_MANIFEST.json`。其他参考项目的源码仅在手动执行 `python tools/bootstrap.py --sources-only --all-sources` 时下载，不是文件夹混剪的依赖。

视觉模型模板为百炼北京地域 `qwen3-vl-plus`，可在 AI 设置中修改服务地址与模型名。把 Key 填到本机 `.env`：

```dotenv
MIXCUT_API_KEY=
MIXCUT_ASR_KEY=
```

或设置同名环境变量。API Key 不写入计划，`.env` 已被 Git 忽略。视觉请求会向所配服务发送抽取的视频帧、分类提示和可能的转写文本；转写请求会发送对应音频。只在明确执行分析后调用服务，费用与限额由服务商和账号决定。

```powershell
.\run.bat --config "C:\MyProject\ai-config.json" collect --limit 5
.\run.bat --config "C:\MyProject\ai-config.json" analyze
```

也可导入本地视频、手工标注片段，不依赖在线模型。文件夹主流程及其本地曲库功能不需要 API Key。
