# 第三方代码、元数据和运行组件

MixCut 原创代码采用 Apache-2.0。以下上游代码和数据保留原许可。固定提交和下载归档摘要见 `VENDOR_MANIFEST.json`。

## 随仓库分发

| 来源 | 许可 | 内容与修改 |
| --- | --- | --- |
| [GuanYixuan/pyJianYingDraft](https://github.com/GuanYixuan/pyJianYingDraft/tree/1e228479dda361e312e85f6037002346cf6dca9a) | Apache-2.0 | `vendor/pyJianYingDraft/pyJianYingDraft` 的 SDK 源码及模板；原许可在同目录上一级的 `LICENSE`。MixCut 修改 `__init__.py`，支持 `PYJIANYINGDRAFT_NO_UI=1` 时跳过 Windows UI Automation 导入。 |
| [isYangs/jianying-editor-skill](https://github.com/isYangs/jianying-editor-skill/tree/b7225641d39a9cd354ec265e2f6101b009fa1b44) | MIT | 音乐名称、ID、时长和分类的元数据。Copyright (c) 2026 luoluoluo22；完整声明在 `assets/jianying/MUSIC_METADATA_LICENSE.txt`。不包含媒体地址或音频。 |

剪映特效索引基于 pyJianYingDraft 的原生资源元数据；人工整理的氛围、动感候选与参数位于 `assets/jianying/`，不是官方完整分类或资源可用性保证。

`face_cover.json` 和 `compound_wrapper.json` 是兼容客户端草稿格式所需的最小字段模板，已移除缓存路径、账号和用户媒体。墨镜模板来自独立测试草稿，名称“拽酷墨镜”；验证客户端标为“不可商用”，默认关闭。未分发剪映效果二进制。相关来源和脱敏说明见 `assets/jianying/SOURCES.json`。

云端音乐 `type/music_id` 字段约定参考 [aoguai/pyJianYingDraft](https://github.com/aoguai/pyJianYingDraft/tree/69cee12ae3f7442416df16acdf368ae574764811)（Apache-2.0）；MixCut 使用独立适配器。

## 可选上游参考与下载

| 项目 | 许可 | 用途 |
| --- | --- | --- |
| [socai](https://github.com/socai-io/socai) | Apache-2.0 | 可选采集 CLI；启用采集安装参数后下载，依赖浏览器和平台兼容性。 |
| [VideoMatrix](https://github.com/w2968066/VideoMatrix--) | MIT | 混剪与任务处理流程参考；不随本库分发其完整应用。 |
| [FireRed-OpenStoryline](https://github.com/FireRedTeam/FireRed-OpenStoryline) | Apache-2.0 | 拆分和内容理解流程参考；本工具未加载其整套模型。 |

需要参考源码时可用 bootstrap 的 `--all-sources`，下载的源码保留原始许可；默认仅需要草稿 SDK。第三方 manifest 记录原始来源，不表示所有上游项目均已打包分发。

## 外部运行组件

FFmpeg、FFprobe 和可选 socai 二进制在安装阶段下载到 `.runtime/`，不随 Git 仓库分发。当前来源为 `imageio-ffmpeg` Windows wheel、`@ffprobe-installer/win32-x64` 和 socai 官方发行包，安装器校验下载摘要。各自的构建许可、源码提供及再分发要求独立适用；安装脚本不授予额外权利。不要直接把本地 `.runtime` 打包再发布。

Python 依赖见两个 requirements 文件，沿用各自许可。项目羽毛图标由图像生成工具制作，生成说明在 `assets/icons/README.md`。程序演示使用合成图片和音频。

开源代码许可不授予第三方视频、音乐、剪映资源、平台账号或模型服务的使用权。请自行确认使用范围及素材授权。
