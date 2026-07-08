# SS1 Ground Station 打包说明

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
dist\SS1GroundStation\SS1GroundStation.exe
```

## 2. 生成安装器

安装 Inno Setup 6 后运行：

```bat
installer\build_installer.bat
```

或者打开：

```text
installer\SS1GroundStation.iss
```

点击 Compile。

输出安装器：

```text
installer\output\SS1GroundStation_Setup_v0.4.0.exe
```

## 3. 安装器会做什么

安装时可以选择：

1. 程序安装目录  
   默认：`%LOCALAPPDATA%\Programs\SS1 Ground Station`

2. 日志和数据目录  
   默认：`Documents\SS1_host_computer_data`

程序会在数据目录下创建：

```text
SS1_host_computer_data\
├─ logs\
└─ data\
```

安装器会写入：

```text
<程序安装目录>\config\user_paths.json
```

程序启动时会读取这个文件，决定 `logs` 和 `data` 的位置。

## 4. 卸载

Inno Setup 会自动生成卸载器，并在开始菜单创建“卸载 二代飞控地面站”。

默认不会删除用户选择的数据目录，避免误删飞行日志和处理结果。
