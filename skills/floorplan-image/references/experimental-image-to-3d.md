# 历史图生3D实验（当前流程不采用）

2026-11-04用户明确不采用运行图生3D模型的路线。以下仅保留旧实验的命令与版本记录，不是当前Skill的执行步骤；不要因为读到本文件就下载模型、部署环境或启动推理。

当前流程请读 [家具照片与自定义模型](furniture-image.md) 及 [模型库检索与适配](furniture-retrieval.md)。

## 生成真实网格

优先使用用户已经提供且来源清楚的 GLB/OBJ 等静态网格；只有照片时，使用开源单图重建方法。首个接入后端是 [TripoSR 官方仓库](https://github.com/VAST-AI-Research/TripoSR) 及 [官方权重](https://huggingface.co/stabilityai/TripoSR)。其官方说明将代码和权重按 MIT 开放；输入照片和其他网格的使用条件仍须另行记录。

在独立环境中按官方安装说明运行推理，保留实际命令、代码 commit、权重 revision/hash、输入图 hash、预处理方式和生成结果。GPU/依赖安装与执行位置遵循当前项目约定，不要求所有用户都有相同机器。HTML 不会自行下载权重或调用重建服务。

### 图片重建编排入口

在安装好官方 TripoSR 依赖的隔离 Python 中运行随 Skill 提供的 `scripts/image_to_furniture.py`。`--triposr-root` 指向包含官方 `run.py` 的源码目录；`--weights` 指向本地 `config.yaml` 和 `model.ckpt` 所在目录。此脚本不自动安装环境或下载权重；缺少前置条件时先按官方文档准备，不改动项目已有的系统 Python。

先核对当前家具 ID 与尺寸依据。将下面变量改为本次实际路径与已核实的版本；`TRIPOSR_CODE_REVISION` 可由源码仓库的 `git rev-parse HEAD` 核对，权重 revision 取自实际下载记录。照片许可单独填写，不用 MIT 代替未知图源许可。

```sh
FURNITURE_PYTHON="/path/to/triposr-venv/bin/python"
FLOORPLAN_SKILL="/path/to/skills/floorplan-image"
TRIPOSR_CODE="/path/to/TripoSR"
TRIPOSR_WEIGHTS="/path/to/local-weights"
FURNITURE_IMAGE="/path/to/furniture.png"
FURNITURE_PLAN="/path/to/plan.json"
FURNITURE_OUTPUT="/path/to/new-empty-output"
TRIPOSR_CODE_REVISION="actual-code-commit"
TRIPOSR_MODEL_REVISION="actual-model-revision"
FURNITURE_IMAGE_LICENSE="unknown"

"$FURNITURE_PYTHON" "$FLOORPLAN_SKILL/scripts/image_to_furniture.py" \
  --image "$FURNITURE_IMAGE" \
  --triposr-root "$TRIPOSR_CODE" --weights "$TRIPOSR_WEIGHTS" \
  --asset-id A_CHAIR_01 --height-mm 900 \
  --plan "$FURNITURE_PLAN" --item-id I_CHAIR \
  --code-revision "$TRIPOSR_CODE_REVISION" \
  --model-revision "$TRIPOSR_MODEL_REVISION" \
  --image-license "$FURNITURE_IMAGE_LICENSE" \
  --background auto --output "$FURNITURE_OUTPUT"
```

示例中尺寸和家具 ID 需要替换；输出必须是新的空目录，不能覆盖旧推理记录。传入 `--plan` 时必须同时提供 `--item-id`，脚本在启动 GPU 前检查该家具存在。原计划文件不会被修改。

**首次不要猜 `--front-axis`。**省略它时脚本只重建原始网格，`run.json` 的状态为 `needs_alignment`；这不是替换完成，不应把旧参数模型的页面当作结果。查看 `reconstruction/0/mesh.glb`，确认上轴及靠背/正面后，继续下节的 `prepare → attach → build`，无需重跑推理。从 `run.json` 将代码/权重版本、权重 hash、图源使用条件和预处理记录整理成资产 `provenance.json`；不复制 `command`、原始网格绝对路径或其他执行路径。完整命令保留在本次运行的 `run.json` 中。

如果同一输入的模型轴向已经实际核对，可在上面的命令增加 `--up-axis z --front-axis=-x`，一次生成资产；这里 `-x` 仅为参数写法示例，不能直接当作新照片的朝向。提供计划和家具 ID 时进一步生成 `plan.json`、`index.html`。未提供计划时只生成资产，不自动猜应替换哪件家具。代码/权重版本和照片许可无法核实时保持 `unknown` 并报告，不编造来源。

`--background auto` 使用现有透明层，普通不透明图片则调用 `rembg/u2netp`；前景会居中缩放并合成灰底。`--background alpha` 要求已有透明背景；`--background none` 依赖用户已准备好干净灰底、主体比例和留白，不能当作跳过背景检查的捷径。默认网格提取分辨率为128，可用 `--mc-resolution` 在64～256间调整；提高分辨率不保证细节准确，还可能超过资产面数限制。

当前真实照片技术验证使用官方透明椅子样图，图源许可未明确，仅作内部验证；不能据此宣称所有普通 RGB 照片、抠图场景或家具类别都已经实测通过，也不把该样图默认用作公开宣传素材。

本次实际验证的运行组合为 Python 3.10、PyTorch 2.2.0+CUDA 12.1、TripoSR commit `107cefdc244c39106fa830359024f6a2f1c78871`、权重 revision `5b521936b01fbe1890f6f9baed0254ab6351c04a`。权重 `model.ckpt` 的 SHA256 为 `429e2c6b22a0923967459de24d67f05962b235f79cde6b032aa7ed2ffcd970ee`，约 1.68GB。这是一组已跑通的参考组合，不要求升级或替换用户现有可用环境；先检查依赖和 CUDA 是否匹配。此旧版后端实际使用 NumPy 1.x、Transformers 4.35，不能假定任意最新依赖都兼容。

`torchmcubes` 是独立 MPL-2.0 依赖。本次固定 `a9ac95aa329957ba646e7bb057f825f5dfa77f38`；其最新构建默认 C++20，在 CUDA 12.1/GCC9/PyTorch2.2 上需仅将 CMake 的 C++/CUDA 标准改为17并保留补丁。其他工具链先按自身要求判断，不盲目套用此补丁。TripoSR 还需 DINO 的架构 `config.json`（`facebook/dino-vitb16`，本次 revision `f205d5d8e640a89a2b8ef0369670dfc37cc07fc2`）；其已训练权重在 TripoSR checkpoint 中，无需重复下载整套 DINO 权重。离线运行时先缓存配置，再设置 Hugging Face/Transformers 的离线环境变量。

使用真正 Git checkout 时从该仓库读取 commit；若用源码归档，应显式记录归档对应的 commit/hash，不能在不含 `.git` 的子目录运行 `git rev-parse` 并误把父级项目的 commit 当成 TripoSR 版本。环境、依赖锁定和修改补丁与重建产物一起保留。

自动抠图只能作为候选预处理，Agent 必须实际查看原图与准备图，特别检查椅背、座面和支腿之间的镂空。测试已发现白底椅子的细小空隙会被 rembg 填成前景，整体遮罩重合较高也不能掩盖这个局部拓扑错误。质量不合格时保留原家具，优先使用透明背景图、改进抠图或换更清晰背景；不要把这个中间结果直接送入重建后声称成功。

可先执行不运行GPU的预处理并查看输出：

```sh
"$FURNITURE_PYTHON" "$FLOORPLAN_SKILL/scripts/furniture_assets.py" preprocess \
  --source-image "$FURNITURE_IMAGE" --output <output>/preprocessing/input.png
```

`image_to_furniture.py` 遇到普通不透明RGB并自动抠图时，会停在 `needs_preprocessing_review`，尚未运行重建。Agent 自己完成视觉复核后，可在新的产物目录中给同一原图加上 `--prepared-image <output>/preprocessing/input.png` 继续，原图hash仍保留。这个检查是Agent的处理步骤，不是额外的用户授权流程；无法判断时才说明缺口或请求更合适的图片。预处理通过也不保证单图重建的不可见面准确。

检查推理结果再接入：确认主要轮廓、支腿/扶手/靠背等关键部件、背景是否误入网格、正背面、破洞及悬空结构。单图背面是模型推测；残缺或严重偏离原图时调整抠图/输入视角或改用更适合的开源后端，不用默认参数家具冒充成功结果。模型未运行或失败时保留原家具并报告缺口。

原始网格、预处理图、模型权重和用户照片默认保留在运行产物目录，不提交公开仓库。原照片不会因为生成 GLB 就自动获得其他用途的授权。
