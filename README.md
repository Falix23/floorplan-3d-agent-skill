# floorplan-3d-agent-skill

从户型图生成可编辑的2D/3D页面，也可按心仪家具照片匹配合法近似网格，替换床、椅、桌、沙发等指定家具。支持完整计划加载、几何编辑、校验与单文件生成。[English](README.en.md)

基于 [wy51ai/floorplan-3d](https://github.com/wy51ai/floorplan-3d)，保留原作者 `Copyright (c) 2026 wuyi` 及完整MIT许可证。

## 使用方式

将 `skills/floorplan-image` 文件夹复制到 Agent 的技能目录，例如 `~/.codex/skills/floorplan-image`。对有视觉能力的 Codex/Agent 输入一张清晰平面户型图，并说：

> 使用 $floorplan-image，把这张图还原成可编辑的 2D/3D 户型页面。按原图映射家具类型、位置、朝向与尺寸；区分实测和估计，输出 HTML、JSON 和叠图。

Agent 根据 SKILL.md 识别图片，再运行 Python 脚本。HTML 自身不调用 AI API，也不包含 API 密钥。户型和家具照片由当前Agent识别；家具走已有模型检索与适配，不要求部署图生3D模型或下载权重。单张室内实景照不能唯一恢复完整户型，当前几何仅支持正交墙体。

图片还原默认检查家具朝向证据、座椅面向目标及接触/自由摆放关系，拒绝同外包框180度反向或缺失证据的方案。贴墙只作用于指定源图接触边，临窗与分隔柜单列；用户主动编辑后保留证据历史并解除旧约束。流程和边界见[家具约束](skills/floorplan-image/references/furniture-constraints.md)，机械通过不等于独立图像识别准确率。

## 多层户型与家具约束

新增建筑v2数据，逐层携带完整单层计划、标高、净高、板厚与对齐依据。2D可切换楼层、核对下层轮廓；3D提供单层编辑、按真实标高叠层与示意分层展开。房间支持真实楼板洞口，面积扣除挑空；直跑和带平台折返/转角楼梯按显式梯段生成，并检查总升高与上层楼板遮挡。

照片识别仍由Agent逐图完成，不把自制测试通过当成真实别墅识别准确率。已知的扇形/转角踏步可按显式多边形表达；旋梯细节不清时仅作占地与连接示意，不能称为逐级准确重建。当前不支持斜墙、曲面楼梯自动恢复、局部错层、屋顶建模或自动跨层漫游。完整多层流程与图片清单见 [多层Skill指南](skills/floorplan-image/references/multi-floor.md)。

高度复核使用实际3D网格与设定净高，并保留全高墙对照。剖切墙只降低墙体显示高度，家具保持原高；界面会注明剖切上限，避免把1.2m剖切墙当作真实墙高。平面图未给出的柜高、窗高和层高继续标为估计；此项验收不等于建筑净空/规范校核，编辑器仍允许用户设计超高物体。

可选的scikit-image审计将共同结构点分为拟合与留出复核两组，报告当前建筑及建议变换各自的RMS/最大/P95像素误差，核对建筑内嵌原图与offset，不自动修改楼层。运行方法见多层指南；可选依赖要求Python3.9+。借鉴openPlan3D的测试维度形成了[多层验收矩阵](skills/floorplan-image/references/multi-floor-acceptance.md)，区分已覆盖回归和未实现的楼层管理交互。

```sh
python3 skills/floorplan-image/scripts/plan_tools.py build examples/multi-floor-duplex.json --output outputs/duplex.html
python3 skills/floorplan-image/scripts/building.py building-manifest.json --output outputs/building.json --html outputs/building.html --report outputs/building-report.json
```

## 图片还原与贴墙复核

Skill 现在先记录原图像素观测，再换算毫米。显式指定床头/柜背接触的墙面，保持居中家具原位；校正器拒绝穿墙、吸到门窗洞口、朝向冲突或过大的位置修正。

```sh
python3 skills/floorplan-image/scripts/reconstruct.py from-pixels observations.json --output plan.json --report fidelity.json
python3 skills/floorplan-image/scripts/plan_tools.py build plan.json --reference-image source.png --output outputs/index.html
```

页面新增“原图叠加”和3D“正俯视”。模型的水平占地与2D宽深保持一致；正俯视暂时隐藏厨房上柜/烟机，方便对照下方台面。用户手动移动或改尺寸后会解除旧贴墙约束；重新生成的计划也不会被旧浏览器缓存覆盖。

原图与识别结果默认保留在输出目录，不纳入公开仓库。框一致性指标衡量观测数据的几何还原，不能代表独立的图像识别准确率。详细格式见 [像素观测与贴墙协议](skills/floorplan-image/references/pixel-observations.md)。

## 按心仪照片自由替换家具

可以把喜欢的床、椅、桌、沙发等家具外观放进已有户型：交给Agent一张单件照片或带背景的手机随拍，指定目标家具，也可提供合法网格。Agent实际看图识别可见结构，比较许可合适的近似库模型，适配朝向、比例与材质，再从同一网格生成2D俯视图和3D资产。此流程不需要额外图生3D推理、GPU或权重；不保证任意图片都能匹配或找到原款，无法匹配时保留原家具并说明缺口。

给新照片和已有计划的一句调用示例：

> 使用 $floorplan-image，按这张手机照片，在我提供的 plan.json 中定位并替换「房间/家具名称」。先看图、比较合法候选，按可见材质分别匹配照片配色，说明剩余差异，按规格或声明的原生比例工作尺度适配；先预检，再事务替换。输出可编辑HTML、完整JSON、来源和差异，并实际验证移动、旋转、尺寸/高度、分材质改色、撤销、导出导入和刷新；匹配不了的家具保持原样。

Agent按 [家具照片指南](skills/floorplan-image/references/furniture-image.md) 准备资产与替换清单，候选选择见 [检索与适配](skills/floorplan-image/references/furniture-retrieval.md)。批量入口只读取本地已准备资产，不代替Agent识图或自动搜索全网：

```sh
python3 skills/floorplan-image/scripts/replace_furniture.py \
  --plan plan.json --manifest replacements.json --output outputs/replacement-case --dry-run
python3 skills/floorplan-image/scripts/replace_furniture.py \
  --plan plan.json --manifest replacements.json --output outputs/replacement-case
```

预检只向标准输出返回JSON，不写文件。成功事务原子创建一个尚不存在的新目录，包含替换前后计划、含资产HTML、替换报告、可重跑清单和包内资产；任一替换失败时不生成该目录，输入计划不变。清单可逐项选择 `replace` 或 `keep`；改尺寸、位置或朝向需记录依据。实际网格投影筛查墙、门和邻近家具，允许叠放需逐对说明；这是近似筛查，不能替代完整3D碰撞或人体工学验证。已有拆墙/测量状态的完整导出请走页面资产导入，批量脚本会拒绝静默丢弃这些状态。

照片替换默认跟随照片的主要配色。Agent先按软包、金属脚、木边等可见材质分区记录色样与依据，再用 `furniture_assets.py materials --mesh <mesh>` 枚举候选材质，按 [配色映射](skills/floorplan-image/references/furniture-image.md#照片配色与分材质映射) 处理主体颜色并保护未选材质。源材质不能可靠分区或照片本色不明时报告缺口；除非用户要求保留库款颜色，不能仅把明显色差列为差异就算完成。照片光照与白平衡未知，仍不承诺实物色卡级一致。

资产由真实网格经 `furniture_assets.py prepare` 制作，优先使用 `--material-mode preserve-pbr --texture-max-size 256`，需要配色时增加 `--material-overrides <recipe.json>`。这会同时更新GLB与同网格俯视图，保留UV/PBR及未选材质，并分别记录纹理缩放与配色改动。完整计划通过 `furnitureAssets` 内嵌资产、家具通过 `assetId` 引用；2D与3D共享几何、占地和朝向。照片不可见的结构与无标注尺寸仍是未知或估计，2D颜色采样也不保留全部PBR细节。实际HTML还需完成编辑、撤销、完整JSON导出、全新会话导入及刷新验收；保存失败时依提示备份完整JSON/HTML。

浏览器家具面板提供「3D操作」移动、旋转、缩放整件家具，以及自定义模型的「模型材质」分区改色。点击「应用材质修改」后，页面准备新的嵌入资产与2D俯视图，再一起写入可撤销的修改；只影响当前实例，完整JSON也携带新配色。该编辑范围是家具整体和材质，不包含网格顶点或扶手、桌腿等部件建模。

用户图片、下载原模型和运行产物默认留在独立输出目录，不纳入公开仓库。照片使用条件、模型许可、作者、原文件哈希与适配改动分别保留；库模型匹配不等于同款精确重建。

## 不通过 Agent，直接加载数据

```sh
python3 skills/floorplan-image/scripts/plan_tools.py validate examples/two-room.json
python3 skills/floorplan-image/scripts/plan_tools.py build examples/two-room.json --output outputs/two-room.html
python3 -m http.server 18763 --bind 127.0.0.1
```

浏览器访问 `/outputs/two-room.html`。根目录 `index.html` 仍可直接打开查看原始示例，也可从“文件 → 导入方案”加载任意完整计划。

- 家具：拖动、旋转、调整宽深高，或使用3D操作手柄；自定义模型可按材质改色，在2D和3D之间同步。
- 建筑：文件 → 编辑户型数据，修改房间多边形、墙矩形、门窗；可撤销。
- 导出方案包含完整建筑、家具、拆墙和测量；导入和刷新恢复。旧版仅家具 JSON 不再接收。
- 墙体未知承重属性用 `u`，页面不允许直接拆除。
- 生成单文件内嵌计划和完整 MIT 许可；Three.js 通过 CDN 加载，3D需要联网。

数据协议与家具映射见 `skills/floorplan-image/references/`。基础户型生成器仅依赖 Python3.8+ 标准库；可选家具资产预处理使用 `trimesh`、`Pillow` 等依赖，当前检索与适配流程不依赖图生3D推理环境。技能模板 `assets/index.html` 应与根目录 HTML 保持一致，测试会检查。

## 验证

```sh
python3 -m pip install -r skills/floorplan-image/scripts/requirements-furniture.txt
python3 -m pip install -r skills/floorplan-image/scripts/requirements-alignment.txt
python3 -m unittest discover -s tests -v
```

CI还安装 `playwright` 及匹配的 Chromium，并运行以下浏览器验收；本机可按相同步骤执行：

```sh
python3 tests/browser_multi_floor.py
python3 tests/browser_multifloor_acceptance.py
python3 tests/browser_furniture_constraints.py
python3 tests/browser_smoke.py
python3 tests/browser_furniture_assets.py
python3 tests/browser_furniture_assets.py --targeted
python3 tests/browser_furniture_assets.py --bad-png
```

脚本启动独立临时端口和无登录浏览器，检查计划与家具编辑、导入导出、撤销、刷新、3D资产及损坏PNG回退。结果与截图保存于 gitignored `outputs/`。

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
