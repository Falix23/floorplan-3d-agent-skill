# 多层能力验收矩阵

参考项目为 [laanlabs/openPlan3D](https://github.com/laanlabs/openPlan3D)，固定到提交 `98f9cb467e4045c6abc5d9880ada1f03dff50c68`。借鉴其测试维度，测试代码按本项目v2建筑/毫米协议独立编写，不复制其运行实现、默认层高、兼容策略或原生桥接协议。上游许可为MIT，Copyright (c) 2026 theLodgeStudio。本项目原作者wuyi与MIT声明保持不变。

已核对的上游文件：

- [floors.test.ts](https://github.com/laanlabs/openPlan3D/blob/98f9cb467e4045c6abc5d9880ada1f03dff50c68/tests/floors.test.ts)：楼层数据隔离、编辑状态与撤销、层高/板厚修改等。
- [floor-stack.test.ts](https://github.com/laanlabs/openPlan3D/blob/98f9cb467e4045c6abc5d9880ada1f03dff50c68/tests/floor-stack.test.ts)：活动层不应改变叠放标高、地下室与层间间隔、下层引用次序。
- [floor-openings.test.ts](https://github.com/laanlabs/openPlan3D/blob/98f9cb467e4045c6abc5d9880ada1f03dff50c68/tests/floor-openings.test.ts)：洞口面积、撤销与序列化。
- [floor-view.spec.ts](https://github.com/laanlabs/openPlan3D/blob/98f9cb467e4045c6abc5d9880ada1f03dff50c68/tests/browser/floor-view.spec.ts)：桌面/手机切层后的取景及视图状态。
- [BUG_REPORT.md](https://github.com/laanlabs/openPlan3D/blob/98f9cb467e4045c6abc5d9880ada1f03dff50c68/BUG_REPORT.md)：历史测试曾发现楼梯保存丢失等情况，用于提醒检查刷新后的完整状态；不声称历史缺陷在该项目当前版本仍存在。

## 当前自动化覆盖

| 验收问题 | 本项目证据/入口 | 范围 |
| --- | --- | --- |
| 楼层数组乱序、负标高或缺号，叠放及最近下层是否一致 | `tests/browser_multifloor_acceptance.py` | 四层自制夹具；含不同层间高和平移 |
| 切换不同活动层后，整栋标高是否漂移 | 同上，读取实际Three.js楼层根对象与楼板射线 | 使用相对最低楼层的展示标高，不照搬上游坐标单位 |
| 非首层无家具、缺省可选资产/原图时是否报错或泄漏前层数据 | 同上；先切换两张不同原图，再切无图/无家具层 | 仍有有效房间，不冒充“完全空几何楼层”能力 |
| 手机/桌面切层后新户型是否位于视野内 | 同上；实际SVG viewBox包含每层房间 | 自动适配取景；不宣称已保存每层相机姿态 |
| 楼板洞口的面积、几何和撤销是否一致 | 同上；实际JSON编辑器、净面积差值、undo/redo、WebGL射线 | 测试修改目标层，并核对其余楼层不变 |
| 刷新后洞口、整栋状态是否恢复 | 同上，刷新后的完整JSON与实际楼板射线 | 不是只检查holes字段还在 |
| 无效楼层引用是否破坏当前状态 | 同上 | 无效切层明确拒绝，无自动猜测 |
| 每层家具、资产改色、移动/尺寸/旋转是否互相污染 | `tests/browser_multi_floor.py` | 同名ID跨层隔离与撤销 |
| 楼梯、资产、每层原图与当前层是否完整保存 | 同上；完整状态导出、新会话导入、刷新与整栋JSON比较 | 防止仅保存活动层；楼梯仍为工作参数 |
| 开口是否被“透明地板”假装留空，顶面标高是否真正到达 | 同上及真实案例的`browser-verification.json` | 实际网格射线与楼梯边界，不替代净空/规范验收 |
| 源图配准拟合是否掩盖真实对齐误差 | `tests/test_floor_alignment.py`与`floor_alignment.py` | 拟合/复核分离；核对当前建筑源图哈希及offset；不自动应用建议 |
| 柜高是否超过设定净高，剖切是否掩盖或篡改家具高度 | `tests/browser_furniture_constraints.py` | 实际网格顶面与全高墙/1.2m剖切墙对照、3.1m超高柜负例、数据不变和手机提示；超高由验收检出，不宣称编辑器自动阻断 |

两组浏览器脚本与对齐单测均纳入CI。真实案例输出保存在gitignored目录；CI仅用自制夹具，不提交第三方户型图。

## 未覆盖能力必须如实记录

家具语义另由 `tests/test_furniture_constraints.py` 和 `tests/browser_furniture_constraints.py` 验收：包括同外包框180度反向拒绝、源图证据缺失、面向目标、实体墙与明确临窗支撑、实际SVG/Three.js语义部件方向、用户编辑后的约束解除与保存。真实案例需另外逐图复核，不能拿工程夹具结果当成图片识别准确率。

- 浏览器新增、复制和删除楼层的完整交互尚未实现，不能通过构造JSON夹具宣称这些UI已通过。独立墙体复制、最后一层删除保护及新增/删除的撤销要在实现时补测。
- 每层相机姿态保存与恢复、连续跨层漫游尚未实现；自动适配视野不等同于恢复上次视角。
- 楼层标高/板厚的浏览器专用编辑面板尚未实现；当前通过完整JSON校验，不把协议支持当作交互支持。
- 楼梯净空、扶手、结构、消防或建筑规范不在当前通过范围内。
- 当前拒绝非法标高、轮廓和洞口，不照搬其他项目的旧版数据自动补值策略。

scikit-image是CPU可选审计依赖，其变换模型文档见[官方API](https://scikit-image.org/docs/0.24.x/api/skimage.transform.html#skimage.transform.SimilarityTransform)。拟合点由Agent基于源图确认；软件残差不能证明识别的对应关系正确，也不能证明两层室内布局相同。
