# 多层户型：复式、别墅与地下室

单层仍使用 v1 计划，多层使用 `schemaVersion:2` 的建筑对象，每层携带完整 v1 `plan`。不要把不同楼层的房间/家具放进一个平面，也不要通过复制同一层冒充不同楼层。当前支持正交墙、直跑及带平台的折返/转角楼梯、室内楼板洞口；斜墙、弧形/旋转楼梯、局部错层和屋顶造型尚未建模。单张室内照不能据此补出未知楼层。

## 输入与图像识别

先实际查看所有原图，辨认楼层名称、上下方向、重复的楼梯井/柱网、户型边界和挑空标记。可以接收一张拼图中的多层或多张独立图：

- 同图多层：分别裁剪，记录每层裁剪范围和原图到本层图的坐标变换；每层重新使用裁剪图像素，不能继续使用整图中另一层的坐标。
- 多张楼层图：每层独立保存图片、像素观测和标定；不同缩放、旋转或镜像须先校正并记录。共同尺寸用于核对比例，不能默认同一页上的两层比例相同。
- 层数不完整：只建立已提供楼层，标出未提供的连接终点；不得补建未知楼层。图示“挑空”不能当作上层房间或补上地板。

沿用 `pixel-observations.md`，各层分别写 `observations.json`，房间增加可选 `holesPx`（数组，每个元素是一个洞口多边形）。洞口通过同层像素标定转成 `rooms[].holes`。不把楼梯当家具类型或矩形地毯。家具、门窗、贴墙关系、模型与配色仍按每层单独识别和复核。

## 共同基准与竖向尺寸

各层内部仍使用本层的毫米坐标。`offset:[dx,dy]` 把本层坐标平移到建筑共同坐标：`buildingXY = localXY + offset`，不改变本层参考图标定。选择同一楼梯井角/柱网交点作为共同基准，至少再用另一处已确认结构核对；来源图不清时写 `alignment.status:estimated` 并说明。方向未确定时先报告，不擅自镜像来凑对齐。

`elevation` 为本层完成楼面标高，可以为负（地下室）。单层 `plan.height` 为本层净高，`slabThickness` 是本层楼面下方楼板厚度，均为 mm。例如下层0、净高2800、上层3000、板厚200时，楼板底正好在2800。不能同时把3000当净高和层间高。标高/净高/板厚缺图示时在 `vertical` 明示工作估计，不说测得。

当前不支持两个楼层使用相同标高，也不支持上下层净高穿过上层楼板的错层表达；遇到这些情况报告协议缺口。房间和家具 ID 只须本层唯一，跨层允许同名同ID；任何修改须同时指明 `floorId`。

## 建筑协议

```json
{
  "schemaVersion": 2,
  "id": "duplex-01",
  "title": "两层户型",
  "units": "mm",
  "floors": [
    {
      "id": "F1", "name": "一层", "elevation": 0,
      "offset": [0, 0], "slabThickness": 200,
      "alignment": {"status": "estimated", "evidence": "楼梯井左上角对齐，柱网待核对"},
      "vertical": {"status": "estimated", "evidence": "净高2800、层间3000、板厚200为工作值"},
      "planFile": "F1/plan.json", "imageFile": "F1/input.png"
    },
    {
      "id": "F2", "name": "二层", "elevation": 3000,
      "offset": [0, 0], "slabThickness": 200,
      "alignment": {"status": "estimated", "evidence": "与一层楼梯井对齐"},
      "vertical": {"status": "estimated", "evidence": "同上，待现场尺寸"},
      "observationsFile": "F2/observations.json", "imageFile": "F2/input.png"
    }
  ],
  "stairs": [],
  "uncertainties": ["楼梯级数尚不清晰，暂不补出连接"]
}
```

`planFile` / `observationsFile` / 内嵌 `plan` 三选一。清单路径相对清单文件；构建完成的建筑统一内嵌 `plan` 与参考图，不依赖本机绝对路径。每层内嵌图片需有 `reference` 像素标定。

```sh
python3 <skill>/scripts/building.py <output>/building-manifest.json \
  --output <output>/building.json --html <output>/index.html \
  --report <output>/building-report.json
python3 <skill>/scripts/plan_tools.py validate <output>/building.json
```

也可直接构建已完成的内嵌建筑：`plan_tools.py build building.json --output index.html`。不要对建筑使用单层 `reconstruct.py`；逐层重建后由 `building.py` 组合。

## 挑空与楼梯

房间 `holes` 是真实楼板洞口：严格在房间内，不得自交、重叠或触碰外轮廓。接外边的挑空改用凹房间轮廓。面积显示扣除洞口；标签/家具中心不能位于洞内。楼梯上层洞口不能通过放一张透明图片代替。多层模式不另画封闭天花，上层的实际楼板表达层间关系；当前不建屋面。

楼梯在建筑级 `stairs` 中表达，XY 使用建筑共同坐标：

```json
{
  "id": "S1", "name": "折返楼梯", "fromFloorId": "F1", "toFloorId": "F2",
  "flights": [
    {"start": [5800,8000], "end": [5800,3800], "width": 900, "rise": 1500, "steps": 10},
    {"start": [7200,3800], "end": [7200,8000], "width": 900, "rise": 1500, "steps": 10}
  ],
  "landings": [{"poly": [[5350,2800],[7650,2800],[7650,3800],[5350,3800]], "elevation": 1500}],
  "evidence": "示例工作几何；实际输入应给出可见箭头、平台、级数和估计依据"
}
```

梯段 `start→end` 沿上升方向，每段只支持一条非零正交直跑。`rise` 为该段升高，总和须等于连接层的标高差。平台 `elevation` 相对于起始低层，不是全楼绝对标高；折返/转角的两段端点须被同标高的平台连接。只连接相邻楼层，跨多层拆成多个连接。上层最后梯段的中部与接近出口的全宽抽样必须位于洞口中；这是几何防堵塞检查，不是完整净空/踏步规范验收。当前没有自动扶手、承重判断、建筑规范或自动跨层漫游能力，楼梯是按观测/工作参数生成的示意。

## 浏览器与逐层复核

- `楼层`切换 2D/3D 当前层，各层家具、房间材质、拆墙与测量独立保存。`下层轮廓`在2D显示按共同基准对齐的虚线，便于核对；它不是下层可编辑副本。
- 3D `单层编辑`支持当前层家具移动、旋转、尺寸和分材质配色。该视图以当前层地面为局部0，标高在楼层栏显示。`整体叠层`按标高显示所有楼层及楼梯；`分层展开`只增加展示间距，不改JSON标高，楼梯在整体模式查看。整体/展开用于检查，回单层再编辑。
- 整体模式检查每层轮廓位置、楼面与净高、真实洞口和楼梯上升方向；按需切矮墙便于观察。逐层检查家具数量、楼梯口、挑空边与门窗，不能只看“加载了几层”。
- 每层分别执行至少一次家具修改/撤销，再切层确认另层同ID实例未改变；保存后用文件菜单导出完整状态，在全新浏览器会话导入、刷新，核对所有楼层、资产、参考图、拆墙与测量。
- `Floorplan.exportPlan()` 返回整栋v2建筑；`activePlan()`只返回当前v1层；`setFloor(id)`切层。`getState()`保留完整编辑状态。不要拿 `activePlan()`冒充整栋备份。

每层叠图使用自己的裁剪图与标定，不能把整栋投影到其中一张图：

```sh
python3 <skill>/scripts/overlay.py <output>/F2/input.png <output>/building.json \
  --floor-id F2 --output <output>/F2/overlay.svg \
  --mm-per-pixel <F2比例> --origin-x <F2原点x> --origin-y <F2原点y>
```

照片家具替换时，`replace_furniture.py`多层清单每条额外填写 `floorId`，仍使用 `schemaVersion:1` / `replacements` 清单协议；一层失败则整栋不输出部分结果。其他字段与单层流程相同。事务入口使用完整 `Floorplan.exportPlan()` 建筑JSON；带拆墙/测量的页面状态备份应保留并在页面中编辑，不能丢弃这些状态。

交付各层原图、观测、叠图、建筑JSON、实际HTML、2D分层和3D叠层/展开截图，列出层间对齐、标高、楼梯级数与未知结构的依据。自制夹具通过不能代替真实复式/别墅图片还原验收；清楚区分工程回归与输入图像实测结果。
