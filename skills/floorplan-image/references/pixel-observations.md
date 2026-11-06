# 像素观测、显式贴墙和相似度复核

先记录实际看见的像素几何，再用脚本换算毫米；保留观测证据，使还原可复查、可重建。

## 输入格式

```json
{
  "id":"image-case-01", "title":"原图还原", "image":{"width":800,"height":600},
  "originPx":[40,30], "height":2800,
  "furnitureReview":{"required":true,"requireFacingTargets":true},
  "calibration":{"mmPerPixel":10,"status":"estimated","evidence":"以图中床长约2000mm估计"},
  "rooms":[{"id":"R1","name":"卧室","polyPx":[[40,30],[440,30],[440,530],[40,530]],"atPx":[300,350],"mat":"wood","floorRotation":90}],
  "walls":[[20,10,460,30,"u"],[20,530,460,550,"u"],[20,30,40,530,"u"],[440,30,460,530,"u"]],
  "windows":[], "doors":[], "slides":[],
  "furniture":[{"id":"I1","type":"bed","name":"双人床","bboxPx":[41,60,241,240],"rot":270,"color":"#8b8067","accentColor":"#e2dcc9",
    "orientationConstraint":{"status":"observed","front":[1,0],"feature":"headboard","maxAngleDegrees":5,"evidence":"源图床头板位于左侧，床尾朝右"},
    "placement":{"mode":"wall","roomId":"R1","edgeIndex":3,"side":"back","gapPx":0,"maxAdjustPx":5,"evidence":"床头与左墙相接"}}],
  "uncertainties":["层高使用默认值；无实测尺寸"]
}
```

`rooms.polyPx`、`atPx` 和家具 `bboxPx` 都以同一张校正后图片的左上角为像素原点。`originPx` 只是转换到工程毫米坐标时扣除的原点，不要在观测坐标中重复扣除。

墙和窗保持 `[x0,y0,x1,y1]` 矩形数组（墙另加 kind）；门改用 `rectPx,hPx,lenPx`，方向向量 `c,o` 与计划格式一致。推拉门使用 `rectPx,v`。

房间若有可信面积标注，可省略 `calibration` 并在相应房间写 `calibrationAreaM2`。转换器用各房间 `面积/像素面积` 的中位数估计统一比例，仍标记 estimated。面积口径不清的标注只写 `sourceAreaM2` 用于报告，不参与比例拟合。不要单凭总建筑面积把所有房间缩放到同一总面积。

家具的 90/270 度会自动交换观测框宽高与局部 w/d。非直角旋转必须另给 `sizePx:[局部宽,局部深]`，不能从轴对齐包围框唯一反推。

## 贴墙条件与计算

源图家具还须遵循 [朝向、使用关系与接触约束](furniture-constraints.md)，开启 `furnitureReview`，记录 `orientationConstraint` 与 `facingTarget`。框误差不能发现180度反向；缺失的语义证据不能由旋转字段本身代替。

`placement.mode:wall` 只用于原图明确接触墙的对象。`roomId + edgeIndex` 指定房间内轮廓的边，从第0个点到第1个点为边0，最后一条边回到首点。轮廓需屏幕顺时针。

`side` 是家具局部接触面：back 床头/柜背，front 正面，left/right 局部左右面；旋转后其朝向必须与该墙面匹配。`gap` 单位mm，或观测中使用 `gapPx`；`maxAdjust` / `maxAdjustPx` 限制观测误差可修正的上限。

校正将中心沿墙法线移动到“半深/半宽 + 指定间隙”的位置，保留沿墙位置、宽深和角度。默认必须有实体墙完整覆盖接触区，不允许自动吸到窗、门洞或客餐厅开放分区；修正后还检查其他墙碰撞。原图明确临窗时，只有显式 `support:window-plane`、源图证据与 `verticalVerified:false` 才允许对齐真实窗面投影，仍不代表窗台与家具高度兼容。超限或朝向矛盾须回看原图，不能靠大幅移动或偷偷旋转过关。

独立家具使用 `mode:free` 或省略 placement；桌椅、地毯、茶几、居中的沙发不应仅因类别而强制靠墙。用户在页面手动移动/旋转/改尺寸后，原图贴墙约束转为 free，保留历史证据，避免再次构建时把用户改动吸回原处。

## 命令

```sh
python3 scripts/reconstruct.py from-pixels observations.json --output plan.json --report fidelity.json
python3 scripts/reconstruct.py refine plan.json --output aligned.json --report fidelity.json
python3 scripts/reconstruct.py audit plan.json --output fidelity.json
python3 scripts/plan_tools.py build plan.json --reference-image source.png --output index.html
```

生成HTML内嵌参考图与像素变换，点击“原图叠加”可调整透明度。重新生成不同计划后使用新的内容指纹隔离旧浏览器缓存；同一计划内的用户修改仍可保存。

## 验收指标与边界

- `wallCollisions` 应为空；`anchorErrorMm` 应接近零。浮点误差不是可见间隙。
- `centerErrorPx` 和 `observedBBoxIoU` 衡量计划与所记录像素框的一致性；不检验这些框本身是否识别正确。仍需实际叠图和人工视觉检查。
- `roomAreas.relativeError` 帮助发现比例或面积口径冲突，不应为了凑面积扭曲轮廓。
- 3D模型按局部水平占地归一；正交俯视关闭透视变形，暂时隐藏厨房吊柜/烟机，普通斜视恢复完整模型。门高、窗台、家具高度、材质与装饰仍是示意。
- 可选 `floorRotation` 控制地板纹理方向；`floorColor` 为3D材质色调。家具可用 `accentColor/frameColor/pillowColor` 表达图中的主要配色，实际模型对支持字段做近似映射。
- 原图、像素观测、计划、识别报告和真实案例截图默认放在 gitignored 输出目录，不自动发布到公开仓库。
