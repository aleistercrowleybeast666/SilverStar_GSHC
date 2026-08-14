# SilverStar_GSHC 打包说明

## 推荐发布形态

使用 PyInstaller `--onedir` 生成程序目录，再用 Inno Setup 生成安装器。

不要使用 `--onefile`，因为 PySide6、pyqtgraph、matplotlib、numpy 体积较大，onefile 启动时需要解压，启动速度明显慢。

## 1. 生成 onedir 程序

在项目根目录运行：

```bat
packaging\build_pyinstaller.bat
```

输出：

```text
dist\SilverStar_GSHC\SilverStar_GSHC.exe
```

## 2. 生成安装器

安装 Inno Setup 6 后运行：

```bat
installer\build_installer.bat
```

或者打开：

```text
installer\SilverStar_GSHC.iss
```

点击 Compile。

输出安装器：

```text
installer\output\SilverStar_GSHC_Setup_v0.0.3.exe
```

## 3. 安装器会做什么

安装时可以选择：

1. 程序安装目录  
   默认：`%LOCALAPPDATA%\Programs\SilverStar_GSHC`

2. 日志和数据目录  
   默认：`Documents\SilverStar_GSHC`

程序会在数据目录下创建：

```text
SilverStar_GSHC\
├─ logs\
└─ data\
```

安装器会写入：

```text
<程序安装目录>\config\user_paths.json
```

程序启动时会读取这个文件，决定 `logs` 和 `data` 的位置。

## 4. 卸载

Inno Setup 会自动生成卸载器，应用、开始菜单项和卸载项统一显示为 “SilverStar_GSHC”。

默认不会删除用户选择的数据目录，避免误删飞行日志和处理结果。

## 5. 发布前界面与导出检查

- QSettings 使用 `SilverStar/SilverStar_GSHC`；
- 支持简体中文 / English 与全局浅色 / 深色主题；
- OpenGL 实时 3D 背景、网格和标签必须跟随主题；
- 导出对话框首次默认全部勾选，导出语言默认 Follow UI；
- 中文与英文产物分别带 `_ZH` / `_EN`，PNG、GIF 和 3D 帧也包含后缀；
- 图内标题、坐标轴和注释按独立导出语言渲染，3D 导出背景按当前主题渲染；
- 传感器详情只读取缓存的 AIR V0 Alignment snapshot，不发送无线查询命令。
