# 家具照片 → 可编辑模型替换

由Agent实际查看家具照片，识别可见结构，选择合法近似库模型或用户网格，再适配并替换已有户型中的指定实例。床、椅、桌、沙发等类别使用同一路由；交付含资产的完整计划、HTML和实际验收记录。无需额外模型推理或权重，无法匹配时保留原家具。

## 先确认要替换什么

实际查看照片及目标户型，确认家具实例ID、类别、可见部件与朝向。室内随拍可以作为参考：先定位目标，保留完整原图及任何辅助裁切记录；背景、阴影和其他家具不属于目标网格。选择与许可核对见 [模型库检索与适配](furniture-retrieval.md)。

默认保留当前中心与朝向；同形外观替换先保留占地。换尺寸或改变形状时，按产品规格、用户尺寸或明确声明的原生比例工作尺度调整，记录依据并重新检查贴墙与布局。单张透视照的像素长宽不能当作真实宽深高；关键尺寸未知且无法判断是否放得下时，保留原家具并说明缺少的信息。

记录遮挡、不可见背面/底部、材质光照影响与尺度不确定性。资产通过 `assetId` 引用，类别仍使用目录 `type`；真实形状变化时同步采用相符类别，例如方桌→圆桌使用 `roundtable`，不能靠类型标签代替几何。

## 选择模型来源

优先使用来源清楚的用户网格；只有照片时按可见结构检索并查看有限候选。缺少关键部件、许可不清或下载不可用时保留原家具，记录原因和可用近似候选；不自动运行模型推理补齐。原图、下载原件、适配文件和运行产物保留在独立输出目录，不加入公开仓库；源码许可不覆盖照片或第三方模型。

## 预处理资产

仅做已有网格预处理时，可在隔离环境通过 `python -m pip install -r <skill>/scripts/requirements-furniture.txt` 准备依赖。准备 `trimesh`、`Pillow`、`SciPy` 等纯CPU几何依赖后，先运行 `furniture_assets.py --help` 查看当前参数。根据实际网格确定上轴与正面轴；两轴不能相同，也不能仅凭文件扩展名猜测。预处理器负责检查并统一网格坐标、生成嵌入式 GLB，以及由**同一网格正俯视渲染**的透明 PNG。输入透视照不能充当该 PNG。

```sh
python3 <skill>/scripts/furniture_assets.py prepare \
  --mesh <output>/selected-library-model.glb \
  --source-image <output>/furniture-source.png \
  --asset-id A_FURNITURE --height-mm "$HEIGHT_MM" \
  --up-axis "$UP_AXIS" --front-axis="$FRONT_AXIS" \
  --method library-retrieval --license "$ASSET_LICENSE" --vertex-color-space auto \
  --material-mode preserve-pbr --texture-max-size 256 \
  --provenance-json <output>/provenance.json \
  --output <output>/asset.json
python3 <skill>/scripts/furniture_assets.py inspect <output>/asset.json
```

本流程通过 `--provenance-json` 记录模型来源/许可、照片使用条件、候选取舍、实际改动、尺寸依据及未知项，格式见检索指南。`method`、`license` 及文件hash由命令写入；已有网格打包记录 `inferencePerformed:false`，不把检索、材质适配或格式转换写成图生3D推理。

模型库资产优先使用 `--material-mode preserve-pbr`：保留源UV、PBR材质和嵌入纹理，作者法线按完整变换的逆转置处理；不改变网格位置或拓扑。`--texture-max-size` 控制嵌入纹理最长边，默认256，可在32～2048间选择。纹理会被有记录地缩小，不能称无损保留原始分辨率；先检查外观和文件体积，再检查浏览器实际保存结果。保留模式的2D PNG仍由同一几何的颜色采样生成，不包含3D的全部PBR光照与细纹理。

原本使用顶点色的网格可选 `--material-mode vertex-color`。两种模式都生成自包含GLB与真实正俯视PNG；顶点色会简化纹理，不能把管线损失当作源资产缺陷。

颜色空间须与真实输入一致：普通规范 GLB/glTF 使用 `--vertex-color-space auto`（默认按 glTF 线性色解释），其他网格默认按 sRGB。只有来源实际表明顶点色是sRGB时才显式使用 `--vertex-color-space srgb`；预处理后 GLB 储存线性色、PNG 输出 sRGB，避免同一模型在2D/3D中出现明显亮度偏差。不要为匹配照片而在不知来源时反复切换颜色空间。

`prepare` 默认输出 384×384 的透明正俯视图；可用 `--resolution` 在 32～1024 间调整。同时保存同名 `.glb`、`.top.png`、`.report.json`，后者包含原始宽深高比例、轴向、归一化包围盒、面数和来源。顶点色模式会损失细纹理；PBR保留模式会按声明的最大尺寸缩小纹理。两者都应对照实际模型说明，不能宣称原款精确贴图。单个资产限制为 GLB 不超过12MiB、俯视 PNG 不超过4MiB、三角面不超过250000；过大时进行有记录的网格简化或纹理缩小，并复核细腿、轮子和占地；不默默截断。图像分辨率限制与浏览器本地存储配额是两回事。

`HEIGHT_MM` 按已说明的尺寸依据填写；`UP_AXIS`/`FRONT_AXIS` 根据检查结果设置，例如上轴为 `y` 或 `z`、正面为 `+x/-x/+y/-y/+z/-z`。`--license` 记录网格许可，照片使用条件另记在 `provenance` 中。

### 非轴向模型的三轴姿态

`--up-axis`/`--front-axis` 只适合源网格的已知正交轴，不会自动消除物体自身的俯仰或左右侧倾。需要三轴刚体对齐时，先从可靠的结构方向、支撑或接地参考确定源网格到规范家具坐标的旋转，再保存带依据的 JSON。不要仅对整个点云做 PCA 就把某一轴当作竖直：靠背可能自然后仰，座面或脚架也可能已经局部重建错误。

```json
{
  "rotation": [[1, 0, 0], [0, 0.984807753, 0.173648178], [0, -0.173648178, 0.984807753]],
  "evidence": "仅为格式示例：只有已核实源模型绕X前倾10度时，才使用这个反向旋转；实际模型必须另行量测。"
}
```

在 `prepare` 命令增加 `--orientation-matrix <output>/reviewed-orientation.json`。矩阵将源坐标列向量转换到 X=宽、Y=高、Z=深；它会**覆盖** `--up-axis` 与 `--front-axis`，而不是叠加在两轴转换后。`rotation` 必须是有限、正交且行列式为 +1 的 3×3 纯旋转；镜像、拉伸、剪切或缺少 `evidence` 会被拒绝。Python 接口对应 `prepare(..., orientation_matrix={"rotation": ..., "evidence": ...})`。输出在 `alignment` 与 `provenance.orientation` 中保留矩阵和依据，不依赖该临时 JSON 的路径。

对齐后分别看正面、侧面和俯视，复核左右/前后没有镜像、模型底部及支撑关系，以及原本合理的自然后仰是否保留。矩阵只负责全局刚体姿态，不能修复座面局部塌陷、变形或悬空脚轮；如果将座面转平会导致靠背过度后仰，须把局部结构问题单独记录，不能宣称整体已修好。重新打包时仍需检查实例宽深高与原比例，不能用额外逐轴缩放掩盖姿态错误。

规范化后的静态网格采用 X=宽、Y=高、Z=深，底部 Y=0，正面朝 +Z、背面朝 -Z；包围盒为 `[-0.5,0,-0.5]` 到 `[0.5,1,0.5]`。浏览器使用家具 `w/height/d` 定义最终尺寸；这是一种外观适配，宽深高分别缩放可能改变原网格比例，应在对照时检查失真。不要用旋转家具实例去掩盖错误的网格轴向。

资产包为一个 JSON 对象，包含 `id`、`format: "glb"`、嵌入 GLB 的 `dataUrl`、正俯视 PNG 的 `topViewDataUrl`、`defaultHeightMm` 和 `provenance`。节点变换须烘焙到顶点（`prepare` 会完成），俯视 PNG 必须可完整解码。不要写本机绝对路径、远程临时链接或依赖旁挂纹理的模型；所有纹理须嵌入 GLB。记录来源、作者、文件与源图hash、改动、已知许可和估计项；不确定项不可写成已测得事实。

## 接入指定家具

Agent完成图像观察、候选取舍和 `prepare` 后，另存识别/匹配证据（也可放入资产 `provenance`），再编写替换清单。事务脚本只处理已准备的本地资产和显式变更，不负责识图、全网搜索或判断是否同款。

`replacements.json` 的最小示例：其中 `itemId` 必须替换成当前计划中的真实实例ID，`asset` 相对清单文件所在目录解析。

```json
{
  "schemaVersion": 1,
  "replacements": [
    {
      "itemId": "I_TARGET",
      "decision": "replace",
      "asset": "asset.json",
      "reason": "可见主体、关键部件和朝向与候选相符；具体差异见资产provenance"
    },
    {
      "itemId": "I_UNCERTAIN",
      "decision": "keep",
      "reason": "关键结构被遮挡，没有合适且许可清楚的候选"
    }
  ]
}
```

- `decision: keep` 保留原家具，不绑定候选；无法匹配时用它记录原因。
- `replace` 可用 `set` 显式修改 `type,name,cx,cy,w,d,height,rot,color,placement`。未填字段保留原值；实例缺少高度时补资产默认高度。尺寸改变必须写 `dimensionsEvidence`，位置、旋转或 `placement` 改变必须写 `placementEvidence`。
- 按 `prepare` 报告的 `sourceExtents` 尽量统一缩放，注明规格、用户尺寸或工作尺度依据；原生比例失真会报告告警，不能把逐轴拉伸当成照片尺寸恢复。形状改变时同步设置相符的 `type`。
- 预检使用实际网格投影筛查墙、门和邻近家具。确有用途依据的允许叠放逐对填写 `allowOverlap: [{"with":"I_NEARBY","reason":"明确的叠放用途"}]`；它是近似投影筛查，不是完整3D碰撞或人体工学校验，失败不能靠扩大阈值掩盖。
- 三角形投影合并所有高度，地毯、桌椅、台面等分层叠放也须逐对写 `allowOverlap` 理由，不按 `type` 自动豁免。局部512栅格在报告中给出 `mmPerPixel`，容差取2mm与一像素对应毫米数的较大值；薄于该误差带的相交记为 `boundary_tolerance` 告警。门仅检查 `rect`，不含门扇完整摆动范围；默认家具按矩形检查。

先预检，再输出到一个**尚不存在的新目录**：

```sh
python3 <skill>/scripts/replace_furniture.py \
  --plan plan.json --manifest replacements.json \
  --output outputs/replacement-case --dry-run
python3 <skill>/scripts/replace_furniture.py \
  --plan plan.json --manifest replacements.json \
  --output outputs/replacement-case
```

`--plan` 接受裸计划或页面导出的完整JSON；若后者包含拆墙/测量状态，脚本会拒绝，避免丢失状态。此时保留原完整备份，在已有页面选中家具，通过属性面板导入准备好的资产JSON并继续编辑，不重建空状态页面。

`--dry-run` 仅向标准输出返回JSON报告，不写文件。正常成功时原子创建目录，包含 `before.plan.json`、`plan.json`、`index.html`、`replacement-report.json`、`manifest.json` 和 `assets/` 内的选中资产包。输出清单使用包内相对路径，可把整个目录移走后，以 `before.plan.json` 为输入重新运行。批量替换任一失败时返回非零及标准输出报告，不生成输出目录，原计划不变。查看逐实例的变更、警告与失败原因，勿把 `keep` 当作已替换。

输入来自图片还原时，先用 `reconstruct.py from-pixels` 复核建筑，再替换家具。底层单件工具仍提供 `furniture_assets.py attach`，随后可自行校验和构建；批量Agent编排默认使用上述事务命令。

完整计划中的 `furnitureAssets[assetId]` 保存可复用资产，目标家具通过 `assetId` 引用；2D与3D共享网格、占地和朝向。完整JSON与HTML必须携带资产，不仅是本机模型路径。字段见 [plan-schema.md](plan-schema.md)。

## 在实际页面验收

1. 在 2D 检查同网格的正俯视图、占地框、朝向与贴墙关系；在 3D 的正俯视、斜视检查模型形状和底部落地。对照源照片说明可见相似处与推测部位，不把“加载成功”当成外观还原验收。
2. 实际移动、旋转、编辑宽深和高度，检查 2D/3D 的中心、方向及占地同步。替换模型后重新检查墙体、门窗与相邻家具碰撞；放大模型不能自动忽略原有通行空间。
3. 实际撤销/重做家具编辑，导出完整 JSON 后在全新会话导入，再刷新核对资产、位置、尺寸和朝向。模型损坏或加载失败时应保留默认模型并提示失败，不清空家具；用一份损坏资产验证该行为。
4. 大型嵌入资产可能超过浏览器本地存储配额。核对实际保存提示，出现失败就导出完整 JSON/HTML 作为备份，不能声称刷新一定恢复。资产嵌入不会消除 Three.js/CDN 的运行依赖。

保留实际HTML截图、导出重导入的计划和检查记录；浏览器或资产处理依赖不可用时列出未验证步骤，不宣称已经验收。多件家具合并和直排→L形、方桌→圆桌的对照规则见 [检索指南](furniture-retrieval.md#形状变化对照)。
