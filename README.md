# SysMonitor — Windows 系统监控悬浮窗

<div align="center">

![图标](app_icon.png)

**圆环仪表 · 阈值变色 · 四边吸附 · 亮暗主题 · 毛玻璃质感**

[![Download](https://img.shields.io/badge/下载-最新版本-green)](../../releases/latest)
[![Platform](https://img.shields.io/badge/平台-Windows%2010%2F11%20x64-blue)]()
[![License](https://img.shields.io/badge/许可-MIT-orange)](LICENSE)

</div>

一款 Windows 桌面悬浮窗系统监控工具：手机电量风格的圆环仪表实时展示 CPU / GPU / 内存 / 磁盘，支持温度监控、阈值变色、靠边吸附、亮暗主题与 Win11 毛玻璃质感。

## ✨ 功能

### 🖥️ 悬浮窗
- **2x2 / 4x1 / 1x4** 三种布局，右键随时切换
- **四边吸附停靠**：拖到屏幕任意一边自动贴边收缩为小环（上下边横排、左右边竖排）
- **悬停展开**：停靠后鼠标悬停自动展开完整数据，移开自动收起
- 显示网速、磁盘读写速率；拖离边缘恢复完整显示

### 📊 圆环仪表
- CPU / GPU / 内存 / 磁盘 使用率 + 5 分钟历史折线图
- **阈值变色**：< 黄阈 绿色，≥ 黄阈 黄色，≥ 红阈 红色（黄/红阈值可调）
- **温度模式**：主页面一键切换百分比 / 温度显示，独立温度阈值
  - CPU：ACPI 热区（管理员）或 LibreHardwareMonitor
  - GPU：NVIDIA (NVML) / AMD (ADL)
  - 硬盘：NVMe/SATA（存储可靠性计数器）
  - 内存：消费级主板无温度传感器，显示 `--`

### 🎨 界面
- **亮 / 暗主题**一键切换，iOS 风格半透明质感
- **Windows 11 毛玻璃**：系统级 Acrylic 背景（DWM Acrylic）
- 主窗口设置：刷新间隔、阈值、主题、悬浮窗开关，配置自动保存

### 🧹 实用工具
- **释放内存**：右键悬浮窗一键触发——gc 回收 + 释放系统文件缓存 + 修剪全系统进程工作集，托盘气泡反馈真实释放量
- **单实例保护**：重复启动自动激活已运行实例的主窗口
- **悬浮窗自愈**：Win+D"显示桌面"后 1 秒内自动恢复，不会凭空消失

## 📥 下载

前往 [**Releases**](../../releases) 页面：

| 文件 | 说明 |
|------|------|
| `SysMonitorSetup-1.0.0.exe` | 安装版（推荐）：向导式安装、开始菜单/桌面快捷方式、可卸载 |
| `sysmonitor-portable.exe` | 便携版：单文件免安装，双击即用 |

> 无需安装 Python，所有依赖已内置。文件通过微信/QQ 传输时请传**单个 exe**，不要只传解压目录里的裸文件。

## 🚀 使用

1. 双击安装或运行便携版，确认一次 UAC（管理员权限）
2. 悬浮窗出现在屏幕右上角，鼠标悬停查看详情
3. 双击悬浮窗（或点托盘图标）打开主窗口
4. 主窗口设置区可调整：刷新间隔、百分比/温度显示、各类阈值、主题

## ❓ 常见问题

**温度显示 `--`？**
- 温度读取需要**管理员权限**（ACPI/存储接口限制），普通权限运行时会有提示
- AMD 显卡需安装官方 Adrenalin 驱动；NVIDIA 需 GeForce 驱动
- 内存条在消费级主板上没有温度传感器，属于硬件限制

**GPU 环显示 `--` 但我有显卡？**
- 确认显卡驱动已正确安装；AMD 需 Adrenalin，NVIDIA 需 GeForce 驱动
- 也可以安装 [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor) 并保持后台运行，程序会自动通过它读取更全面的传感器

**Windows 10 能用吗？**
- 可以。毛玻璃效果需要 Windows 11；Windows 10 上自动降级为纯色主题，其余功能不变

## 🛠️ 从源码运行

```bash
git clone https://github.com/<你的用户名>/sysmonitor.git
cd sysmonitor
pip install PySide6 psutil wmi pywin32 pyadl pillow
python main.py
```

可选依赖：`pynvml`（NVIDIA GPU）

## 📦 构建

```bash
# 1. 独立程序(onedir / onefile)
pyinstaller --noconfirm --windowed --uac-admin --name sysmonitor --icon icon.ico \
  --add-data "app_icon.png;." --hidden-import win32timezone --hidden-import wmi \
  --hidden-import win32com.client --hidden-import PySide6.QtNetwork main.py

pyinstaller --noconfirm --windowed --uac-admin --name sysmonitor-portable --icon icon.ico \
  --add-data "app_icon.png;." --hidden-import win32timezone --hidden-import wmi \
  --hidden-import win32com.client --hidden-import PySide6.QtNetwork --onefile main.py

# 2. 安装包(需 Inno Setup 6)
ISCC.exe setup.iss
```

## 📄 许可证

[MIT](LICENSE)
