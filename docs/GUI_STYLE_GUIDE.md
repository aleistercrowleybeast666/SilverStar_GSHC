# GUI Style Guide

> SilverStar Python/PySide6 工具的精简规范。完整设计原则以仓库 `AGENTS.md` 和实现测试为准。

## 1. 目标
- 工程信息清晰优先于装饰。
- 简体中文 / English 全量运行时切换；内部 ID 永远稳定且语言无关。
- Light/Dark 必须同时覆盖 Qt、2D Plot、OpenGL/3D、Matplotlib/GIF 导出。
- GUI 不包含协议二进制解析、导航数学或控制算法。
- 长任务后台执行，UI 线程只渲染状态。

## 2. 主窗口
推荐：菜单栏 → 深蓝品牌栏 → 左侧导航/主工作区 → 底部状态/进度。产品名和版本稳定显示，打开文件不改变 OS 窗口标题。

## 3. 交互
- 危险操作需要明确确认；普通信息使用非模态状态。
- ComboBox、CheckBox、Table、Dialog 的浅/深色都必须可读。
- 页面重建时阻塞信号，避免在控件自身 signal stack 中销毁父控件。
- 进度必须反映真实已完成工作；失败/取消不能强行显示 100%。

## 4. 数据与绘图
- 实时显示使用有界缓冲；持久化与 GUI 显示生命周期分离。
- 一次数据更新尽量只触发一次绘制 revision。
- 3D 世界坐标、机体坐标、相机和标签含义稳定；主题切换不重建数据模型。

## 5. i18n
- 所有用户可见字符串使用 translation key。
- IMU/GNSS/INS/KF/ENU/CRC/WXYZ 等技术缩写可保留。
- JSON、协议字段、项目 schema、日志 enum 使用规范英文 ID，不随 UI 语言变化。

## 6. 测试
至少覆盖：浅/深主题、中文/英文、窗口尺寸、对话框、长任务进度、取消、异常恢复、图表与 3D smoke test，以及 signal/rebuild 回归。
