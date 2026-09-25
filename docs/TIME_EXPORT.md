# 时间型输出

图片分页长度默认 30 s；GIF 源时长默认 Full（完整任务）。两者均可选 5/10/30/60/120 s、自定义或 Full。
这是输出选择，不限制真实任务运行长度；实时滚动曲线保留其已有有界窗口。

## 图片和目录

PNG 对完整任务按选择的长度分页，Full 为一页。末页保留剩余区间；横轴使用绝对任务时间。
同类图片进入 Position、Velocity、Attitude、Sensors、Diagnostics，文件名包含起止时间和
ZH/EN 语言后缀。PNG 只裁剪绘图输入，不修改解析后的数据、原始日志或完整处理后 TXT。
摘要与 manifest 保留在本次独立输出根目录。

## GIF

源范围从任务起点开始，默认覆盖完整任务；显式选择时长可只取前一段。
运动以固定 30 fps 采样；源时长不超过 30 s 时保持原比例，超过时整个源范围线性压缩为
30 s。画面时间标签始终显示实际任务时间，而非压缩后的播放时间。所有事件使用同一时间映射，不做开伞、着陆或其他事件慢放。轨迹坐标范围固定。
终点单独绘制并保持 30 个物理帧，额外停留 1 s。GIF 的厘秒精度以 30/40 ms 交替表示
30 fps；末尾总时长仍为 1 s。分数秒源长度仅作不超过一个帧间隔的尾端量化。

GIF 与 JSON 时间元信息位于 GIF/，正常导出不生成中间 PNG 或语言帧目录。元信息含
source range、duration、speed、fps、motion/hold frame 数。单个 Matplotlib figure 和两幅
3D axes 在整个任务中复用，每帧更新 artist 后从 RGBA canvas 生成一幅调色板图像并流式
写入 GIF；终点调色板帧重复 30 次。绘制和编码逐帧报告进度并检查取消，不积累所有
RGBA 帧。取消沿用处理器的本次独立输出目录清理，不删除既有输出。

## 兼容与复验

AIR/GSP、telemetry、命令/ACK、串口与握手不变，无新遥测字段或 .ssdecoder 依赖。
GUI 保留既有三页、双语/主题与触屏滚动。CLI 的 --page-seconds 默认 30，
--gif-source-seconds 默认 0（Full）；两者设为 0 都表示 Full，--gif-fps 只允许 30。
实际测试、截图、打包结果只在根 [VALIDATION](../VALIDATION.md)。
