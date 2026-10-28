# MixCut 羽毛图标

- `mixcut-feather.png`：透明底图标源文件。
- `mixcut.ico`：Windows 多尺寸图标（16、24、32、48、64、128、256 像素）。
- 使用内置 `image_gen` 工具，以用户提供的蓝色羽毛图片作为造型参考生成。PNG 原样保留，仅做 ICO 格式转换。
- 桌面入口：`tools/desktop_launcher.pyw`。快捷方式安装：`tools/install_desktop_shortcut.ps1`。

最终提示词：

```text
Use case: logo-brand. Asset type: a polished Windows desktop application icon for MixCut. Input image is a style/subject reference only: preserve its single blue feather, dark navy outline, slender quill pointing down-left and feather leaning slightly right. Redraw as a crisp, elegant blue feather with layered sky-blue and steel-blue vanes, a clean central shaft, confident simple silhouette, and restrained highlights. Center one feather large on a square canvas with comfortable transparent padding; it must remain recognizable at 16, 32 and 48 pixels. No text, no letters, no scissors, no additional symbols, no tile or badge, no background, no heavy shadow. Actual transparent background with clean alpha edges. The only subject is the reference-style blue feather.
```
