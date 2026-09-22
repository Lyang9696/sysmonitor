"""入口:托盘常驻 + 悬浮窗 + 主窗口。"""
import ctypes
import faulthandler
import gc
import json
import os
import sys
import time
import traceback
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from collector import Collector
from main_window import MainWindow
from overlay import OverlayWindow
from theme import apply_glass, qss
from widgets import set_theme

CONFIG_PATH = (Path(sys.executable).parent if getattr(sys, "frozen", False)
               else Path(__file__).parent) / "config.json"
CRASH_LOG = (Path(sys.executable).parent if getattr(sys, "frozen", False)
             else Path(__file__).parent) / "crash.log"


def _init_crash_log():
    """Python 异常与原生崩溃都写入 crash.log,便于定位无窗口模式下的崩溃。"""
    f = open(CRASH_LOG, "a", encoding="utf-8", buffering=1)
    f.write(f"\n===== launch {time.strftime('%F %T')} =====\n")
    faulthandler.enable(f)
    sys.excepthook = lambda t, v, tb: traceback.print_exception(t, v, tb, file=f)


def load_config():
    default = {"interval": 1, "warn": 80, "danger": 90,
               "show_overlay": True, "overlay_pos": None, "theme": "dark",
               "temp_warn": 60, "temp_danger": 80, "ring_mode": "percent",
               "overlay_layout": "grid2", "overlay_dock": None}
    try:
        default.update(json.loads(CONFIG_PATH.read_text("utf-8")))
    except Exception:
        pass
    return default


def save_config(cfg):
    try:
        CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), "utf-8")
    except Exception:
        pass


def _enable_privilege(name):
    """启用当前进程的 Windows 特权(如 SeIncreaseQuotaPrivilege),失败无副作用。"""
    advapi = ctypes.windll.advapi32
    k32 = ctypes.windll.kernel32
    token = ctypes.c_void_p()
    if not advapi.OpenProcessToken(k32.GetCurrentProcess(), 0x0028, ctypes.byref(token)):
        return
    try:
        class LUID(ctypes.Structure):
            _fields_ = [("low", ctypes.c_uint), ("high", ctypes.c_long)]

        class TP(ctypes.Structure):
            _fields_ = [("count", ctypes.c_uint), ("luid", LUID), ("attr", ctypes.c_uint)]

        luid = LUID()
        if advapi.LookupPrivilegeValueW(None, name, ctypes.byref(luid)):
            tp = TP(1, luid, 2)  # SE_PRIVILEGE_ENABLED
            advapi.AdjustTokenPrivileges(token, False, ctypes.byref(tp), 0, None, None)
    finally:
        k32.CloseHandle(token)


def free_memory():
    """真实释放内存:gc + 释放系统文件缓存 + 修剪全部进程工作集(安全软件做法)。
    返回实际换出的工作集大小(MB)。需要管理员权限以覆盖更多进程。"""
    k32 = ctypes.windll.kernel32
    psapi = ctypes.windll.psapi

    class PMC(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_uint), ("PageFaultCount", ctypes.c_uint),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]

    def open_ws(pid):
        """打开进程并返回 (句柄, 当前工作集字节)。打不开返回 (None, 0)。"""
        h = k32.OpenProcess(0x1F0FFF, False, pid)  # PROCESS_ALL_ACCESS
        if not h:
            return None, 0
        pmc = PMC()
        pmc.cb = ctypes.sizeof(pmc)
        ok = psapi.GetProcessMemoryInfo(h, ctypes.byref(pmc), pmc.cb)
        return h, (pmc.WorkingSetSize if ok else 0)

    freed = 0
    # 1) 释放系统文件缓存(缩小再恢复,标准技巧;需要 SeIncreaseQuotaPrivilege)
    _enable_privilege("SeIncreaseQuotaPrivilege")
    k32.SetSystemFileCacheSize(ctypes.c_size_t(1 << 20), ctypes.c_size_t(1 << 20), 0)
    k32.SetSystemFileCacheSize(ctypes.c_size_t(-1), ctypes.c_size_t(-1), 0)  # 恢复默认

    # 2) 枚举全部进程,逐个修剪工作集
    n = 4096
    while True:
        arr = (ctypes.c_uint * n)()
        needed = ctypes.c_uint()
        if not psapi.EnumProcesses(arr, ctypes.sizeof(arr), ctypes.byref(needed)):
            break
        cnt = needed.value // ctypes.sizeof(ctypes.c_uint)
        if cnt < n:
            break
        n *= 2
    me = os.getpid()
    for i in range(cnt):
        pid = arr[i]
        if not pid or pid == 4:
            continue
        h, before = open_ws(pid)
        if not h:
            continue  # 系统保护进程,跳过
        psapi.EmptyWorkingSet(h)
        pmc = PMC()
        pmc.cb = ctypes.sizeof(pmc)
        if psapi.GetProcessMemoryInfo(h, ctypes.byref(pmc), pmc.cb):
            freed += max(0, before - pmc.WorkingSetSize)
        k32.CloseHandle(h)

    # 3) 自身最后修剪
    h, before = open_ws(me)
    if h:
        psapi.EmptyWorkingSet(h)
        k32.CloseHandle(h)

    return freed / 1024 / 1024


def app_icon():
    """程序图标:优先 app_icon.png(打包资源/运行目录),回退自绘绿环。"""
    candidates = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(getattr(sys, "_MEIPASS", "")) / "app_icon.png")
        candidates.append(Path(sys.executable).parent / "app_icon.png")
    candidates.append(Path(__file__).parent / "app_icon.png")
    for c in candidates:
        if c.exists():
            return QIcon(str(c))
    return QIcon(tray_icon_pixmap())


SINGLE_KEY = "SysMonitor.SingleInstance"


def ensure_single(show_main):
    """单实例保护:已有实例运行时,通知它弹出主窗口并返回 False(当前进程退出)。"""
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
    sock = QLocalSocket()
    sock.connectToServer(SINGLE_KEY)
    if sock.waitForConnected(300):
        sock.write(b"show")
        sock.flush()
        sock.waitForBytesWritten(300)
        return False
    QLocalServer.removeServer(SINGLE_KEY)  # 清理上次异常退出残留
    srv = QLocalServer()

    def _accept():
        client = srv.nextPendingConnection()
        if client:
            def _read():
                if client.readAll() == b"show":
                    show_main()
                client.disconnectFromServer()
            client.readyRead.connect(_read)

    srv.newConnection.connect(_accept)
    srv.listen(SINGLE_KEY)
    return True


def tray_icon_pixmap():
    """自绘一个绿色圆环作为托盘/窗口图标。"""
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(15, 17, 27))
    p.setPen(Qt.NoPen)
    p.drawEllipse(QRectF(2, 2, 60, 60))
    pen = QPen(QColor("#4cd97b"), 7, Qt.SolidLine, Qt.RoundCap)
    p.setPen(pen)
    p.drawArc(QRectF(9, 9, 46, 46), 90 * 16, -300 * 16)
    p.end()
    return pm


def main():
    _init_crash_log()
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # 关窗口 = 隐藏,退出走托盘
    app.setFont(QFont("Microsoft YaHei UI", 9))
    icon = app_icon()
    app.setWindowIcon(icon)

    cfg = load_config()
    collector = Collector(interval=float(cfg["interval"]))
    window = MainWindow()
    overlay = OverlayWindow()

    window.load_settings(cfg)
    overlay.set_layout(cfg.get("overlay_layout", "grid2"), emit=False)
    overlay.apply_dock(cfg.get("overlay_dock"))
    if cfg.get("overlay_pos"):
        overlay.move(*cfg["overlay_pos"])
    else:  # 默认出现在屏幕右上角
        screen = app.primaryScreen().availableGeometry()
        overlay.move(screen.right() - overlay.width() - 24, screen.top() + 80)

    def on_overlay_layout(mode):
        cfg["overlay_layout"] = mode
        save_config(cfg)

    def on_snapshot(snap):
        overlay.update_snapshot(snap)
        window.update_snapshot(snap)
        window.update_history(collector)
        # 悬浮窗保活:被"显示桌面"隐藏后自动恢复;停靠在任务栏上时,
        # 点击任务栏会被 Explorer 提到悬浮窗之前,每秒提升一次层级压回去
        if cfg.get("show_overlay", True):
            if not overlay.isVisible():
                overlay.show()
            overlay.raise_()

    def apply_settings(new_cfg):
        cfg.update(new_cfg)
        collector.interval = float(new_cfg["interval"])
        overlay.setVisible(new_cfg["show_overlay"])
        temp_mode = new_cfg.get("ring_mode") == "temp"
        warn = new_cfg["temp_warn"] if temp_mode else new_cfg["warn"]
        danger = new_cfg["temp_danger"] if temp_mode else new_cfg["danger"]
        overlay.temp_mode = temp_mode
        overlay.apply_settings(warn, danger)
        for b in window.boxes.values():  # 主窗口与悬浮窗按同一模式变色
            b.ring.set_thresholds(warn, danger)
        theme = new_cfg.get("theme", "dark")
        dark = theme == "dark"
        set_theme(theme)
        overlay.refresh_theme()
        window.setStyleSheet(qss(dark, apply_glass(window, dark)))
        save_config(cfg)

    def show_main():
        window.showNormal()
        window.raise_()
        window.activateWindow()

    def quit_all():
        cfg["overlay_pos"] = [overlay.x(), overlay.y()]
        cfg["overlay_dock"] = overlay.dock_edge
        save_config(cfg)
        collector.stop()
        tray.hide()
        app.quit()

    # 单实例:重复启动时激活已运行实例的主窗口,当前进程退出
    if not ensure_single(show_main):
        collector.stop()
        return

    collector.snapshot_ready.connect(on_snapshot)
    window.settings_changed.connect(apply_settings)
    overlay.open_main.connect(show_main)
    overlay.quit_app.connect(quit_all)
    overlay.layout_changed.connect(on_overlay_layout)
    apply_settings(cfg)  # 初始应用一次

    # 托盘
    tray = QSystemTrayIcon(icon)
    menu = QMenu()
    act_show = QAction("打开主窗口")
    act_show.triggered.connect(show_main)
    act_quit = QAction("退出")
    act_quit.triggered.connect(quit_all)
    menu.addAction(act_show)
    menu.addSeparator()
    menu.addAction(act_quit)
    tray.setContextMenu(menu)
    tray.setToolTip("系统监控")
    tray.show()

    def on_free_memory():
        freed = free_memory()
        msg = f"内存已释放 {freed:.0f} MB" if freed >= 1 else "内存已释放"
        tray.showMessage("系统监控", msg, QSystemTrayIcon.Information, 1500)

    overlay.release_memory.connect(on_free_memory)

    collector.start()
    overlay.show()
    if "--show" in sys.argv:  # 调试/快捷方式:启动时直接显示主窗口
        window.showNormal()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
