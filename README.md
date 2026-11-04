# floorplan-3d-agent-skill

基于 [wy51ai/floorplan-3d](https://github.com/wy51ai/floorplan-3d)，保留原作者 `Copyright (c) 2026 wuyi` 及 MIT 许可证；新增 Agent Skill、完整户型加载、几何数据编辑、校验与单文件生成。原英文功能说明保留在 README.en.md。

## 使用方式

将 `skills/floorplan-image` 文件夹复制到 Agent 的技能目录，例如 `~/.codex/skills/floorplan-image`。对有视觉能力的 Codex/Agent 输入一张清晰平面户型图，并说：

> 使用 $floorplan-image，把这张图还原成可编辑的 2D/3D 户型页面。按原图映射家具类型、位置、朝向与尺寸；区分实测和估计，输出 HTML、JSON 和叠图。

Agent 根据 SKILL.md 识别图片，再运行 Python 脚本。HTML 自身不调用 AI API，也不包含 API 密钥。户型还原模式无需训练或部署模型；下述家具照片模式需要独立的开源重建环境。单张室内实景照不能唯一恢复完整户型，当前几何仅支持正交墙体。

## 图片还原与贴墙复核

Skill 现在先记录原图像素观测，再换算毫米。显式指定床头/柜背接触的墙面，保持居中家具原位；校正器拒绝穿墙、吸到门窗洞口、朝向冲突或过大的位置修正。

```sh
python3 skills/floorplan-image/scripts/reconstruct.py from-pixels observations.json --output plan.json --report fidelity.json
python3 skills/floorplan-image/scripts/plan_tools.py build plan.json --reference-image source.png --output outputs/index.html
```

页面新增“原图叠加”和3D“正俯视”。模型的水平占地与2D宽深保持一致；正俯视暂时隐藏厨房上柜/烟机，方便对照下方台面。用户手动移动或改尺寸后会解除旧贴墙约束；重新生成的计划也不会被旧浏览器缓存覆盖。

原图与识别结果默认保留在输出目录，不纳入公开仓库。框一致性指标衡量观测数据的几何还原，不能代表独立的图像识别准确率。详细格式见 [像素观测与贴墙协议](skills/floorplan-image/references/pixel-observations.md)。

## 用家具照片替换模型（开发版）

本开发分支增加“家具照片 → 开源重建网格 → 同网格正俯视 2D 图 → 户型资产”的编排入口。它替换已有家具的外观，保留家具语义类别、中心、宽深和朝向，并可单独设置高度。生成的 GLB 与平面图嵌入资产 JSON，再随完整计划和 HTML 携带。该功能尚未作为正式版本发布。

给 Agent 一张清楚的单件家具照片和已有计划，指定要替换的家具，例如：

> 使用 $floorplan-image，把这张椅子照片重建为近似模型，替换 plan.json 中的 I_CHAIR。保留原位置和朝向；宽深沿用计划，高度按产品规格 900mm。使用可在本机/开发机运行的开源方法，输出更新后的 HTML、完整 JSON、真实 2D/3D 截图和外观不确定项。

Agent 按 [家具照片指南](skills/floorplan-image/references/furniture-image.md) 查看图片与目标家具。`image_to_furniture.py` 在独立环境编排本地 [TripoSR](https://github.com/VAST-AI-Research/TripoSR)，记录版本、权重 hash 和来源；首次不指定模型正面时停在 `needs_alignment`，确认原始网格轴向后再预处理、绑定，不能将这个中间状态声称为替换完成。已有合法网格也可直接制作资产。资产准备好后运行：

```sh
python3 skills/floorplan-image/scripts/furniture_assets.py attach \
  --plan plan.json --asset chair-asset.json --item-id I_CHAIR \
  --output outputs/plan-with-chair.json
python3 skills/floorplan-image/scripts/plan_tools.py build outputs/plan-with-chair.json \
  --output outputs/with-chair.html
```

`chair-asset.json` 必须先由真实网格经 `furniture_assets.py prepare` 制作，不能直接把透视照片贴到 2D 图中。模型会统一轴向与包围盒，按实例宽深高适配；首版把纹理采样成顶点色，细纹理会简化，逐轴缩放也可能改变比例，需实际对照。单图看不到的背面、遮挡结构与无标注尺寸仍是估计，不能承诺原款精确复刻。失败时保留原家具，不能用默认模型冒充重建成功。

完整计划通过 `furnitureAssets` 保存可复用资产，家具通过 `assetId` 引用。使用实际页面检查替换、移动、旋转、尺寸/高度、撤销，以及导出后重导入。大型资产可能超过浏览器本地存储配额，须依据页面提示保留完整 JSON/HTML 备份。照片、权重和重建产物默认留在运行目录，不公开提交。TripoSR 的代码/权重许可与输入照片的使用条件分别记录。

## 不通过 Agent，直接加载数据

```sh
python3 skills/floorplan-image/scripts/plan_tools.py validate examples/two-room.json
python3 skills/floorplan-image/scripts/plan_tools.py build examples/two-room.json --output outputs/two-room.html
python3 -m http.server 18763 --bind 127.0.0.1
```

浏览器访问 `/outputs/two-room.html`。根目录 `index.html` 仍可直接打开查看原始示例，也可从“文件 → 导入方案”加载任意完整计划。

- 家具：拖动、旋转、调整宽深，在2D和3D之间同步。
- 建筑：文件 → 编辑户型数据，修改房间多边形、墙矩形、门窗；可撤销。
- 导出方案包含完整建筑、家具、拆墙和测量；导入和刷新恢复。旧版仅家具 JSON 不再接收。
- 墙体未知承重属性用 `u`，页面不允许直接拆除。
- 生成单文件内嵌计划和完整 MIT 许可；Three.js 通过 CDN 加载，3D需要联网。

数据协议与家具映射见 `skills/floorplan-image/references/`。基础户型生成器仅依赖 Python3.8+ 标准库；可选家具资产预处理使用 `trimesh`、`Pillow` 等依赖，图生 3D 另需所选开源后端的独立环境。技能模板 `assets/index.html` 应与根目录 HTML 保持一致，测试会检查。

## 验证

```sh
python3 -m unittest discover -s tests -v
```

浏览器集成验收：安装 `playwright` 及匹配的 Chromium，然后运行 `python3 tests/browser_smoke.py`。脚本启动独立临时端口和无登录浏览器，检查完整计划切换、几何与家具修改、导入导出、撤销、刷新恢复和3D渲染。结果与截图保存于 gitignored `outputs/`。

合成样例只验证工程链路，不代表真实图片识别准确率。真实质量需使用用户图片逐张叠图复核；无尺度输入的面积/造价仅为估计。窗台高度和家具细节主要采用模板默认值。

## 开源与发布

本项目采用 MIT 许可证，允许修改、再发布与商用。分发时保留原作者版权声明和完整 LICENSE；生成的HTML也内嵌许可证与来源链接。引用第三方图片的权限须单独处理。

## 原作者、版权与完整许可证

本项目基于 **[wy51ai/floorplan-3d](https://github.com/wy51ai/floorplan-3d)** 开发，原作者为 **[wuyi（GitHub: wy51ai）](https://github.com/wy51ai)**。原项目的版权声明如下，完整保留：

> Copyright (c) 2026 wuyi

本仓库是由 `Falix23` 维护的衍生版本；图片识别 Skill、完整户型导入及相关增强由本仓库新增，不表示原作者参与或背书。上游原有代码的版权归属及 MIT 授权保持不变。

以下为项目 [LICENSE](LICENSE) 文件的完整原文；生成的 HTML 页面也保留了原作者版权、来源链接与完整许可证。

```text
MIT License

Copyright (c) 2026 wuyi

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
