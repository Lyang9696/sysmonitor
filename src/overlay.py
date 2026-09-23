"""桌面悬浮窗:无边框、置顶、可拖动、四边吸附停靠;2x2 / 4x1 / 1x4 布局。

窗口有三种形态(全程几何动画过渡):
  自由 free     -- 跟随鼠标拖动,完整面板
  停靠 compact  -- 松手时贴近屏幕边缘(≤20px)触发:收缩为贴边小环
  停靠 full     -- 光标在收缩小环上悬停 120ms 展开;移开 600ms 后收回小环
从停靠态往屏幕内拖动即脱离吸附,回到自由态。

窗口几何完全由程序按形态设定(_apply_layout 计算,动画落点强制校验),
布局管理器与 Windows 最小宽度限制均不得干预(见 SetNoConstraint 与
nativeEvent 的注释)——否则贴边 34px 小环会被撑破,环被裁成半个。

dlog() 把每次吸附/展开/收缩的目标与实际几何写入 dock_debug.log,
排查多屏/缩放环境下的错位时打开看。
"""
import ctypes
import sys
import time
from ctypes import wintypes
from pathlib import Path

from PySide6.QtCore import (QEasingCurve, QEvent, QPoint, QPointF,
                            QPropertyAnimation, QRect, QRectF, Qt,
                            QTimer, Signal)
from PySide6.QtGui import QCursor, QGuiApplication, QPainter
from PySide6.QtWidgets import (QGridLayout, QLabel, QLayout, QMenu,
                               QVBoxLayout, QWidget)

from widgets import RingWidget, pal

RING, RING_H = 66, 80    # 正常态环单元
COMPACT = 26             # 停靠收缩态环尺寸
EDGE_SNAP = 20           # 松手时窗口边缘距屏幕边缘 ≤ 该距离则吸附(px)
DOCK_GAP = 4             # 停靠时距屏幕边缘
SHRINK_DELAY = 600       # 悬停展开后鼠标离开→收起的延时(ms)
MODES = ("grid2", "row4", "col4")


def dlog(msg):
    """停靠几何诊断日志(排查多屏/缩放环境下的吸附错位)。"""
    try:
        p = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
        with open(p / "dock_debug.log", "a", encoding="utf-8") as f:
            f.write(time.strftime("%H:%M:%S ") + msg + "\n")
    except Exception:
        pass


def o_wh(w):
    return f"{w.width()}x{w.height()}@({w.x()},{w.y()})"


def r_str(r):
    return f"{r.width()}x{r.height()}@({r.x()},{r.y()})"


_WIN32 = sys.platform == "win32"
if _WIN32:
    _WM_GETMINMAXINFO = 0x0024

    class _POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    class _MINMAXINFO(ctypes.Structure):
        _fields_ = [("ptReserved", _POINT), ("ptMaxSize", _POINT),
                    ("ptMaxPosition", _POINT), ("ptMinTrackSize", _POINT),
                    ("ptMaxTrackSize", _POINT)]


def fmt_speed(kb):
    if kb >= 1024:
        return f"{kb / 1024:.1f} MB/s"
    return f"{kb:.0f} KB/s"


class OverlayWindow(QWidget):
    open_main = Signal()
    quit_app = Signal()
    layout_changed = Signal(str)  # 正常态布局变化(grid2/row4/col4)
    release_memory = Signal()     # 释放内存

    def __init__(self):
        super().__init__()
        self.setWindowTitle("系统监控")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)

        # ---- 状态 ----
        self.mode = "grid2"       # 自由态布局:grid2 / row4 / col4
        self.dock_edge = None     # 停靠边:None(自由) / top / bottom / left / right
        self._dock_state = None   # 停靠形态:None(自由) / "full"(贴边完整) / "compact"(收缩小环)
        self.temp_mode = False    # 温度显示模式:环显示温度而非百分比

        # ---- 拖动 ----
        self._drag = None         # 光标相对窗口左上角的偏移,拖动期间非 None
        self._dragging = False    # 是否已进入拖动(超过 10px 按压死区)
        self._press_gpos = None   # 按下时光标全局位置,用于死区判断
        self._poll = QTimer(self)  # 拖动轮询:16ms 读一次光标位置跟手
        self._poll.setInterval(16)
        self._poll.timeout.connect(self._poll_drag)

        # ---- 几何动画与防抖 ----
        # 三条计时器各管一件事:动画结束锁定几何 / 吸附后忽略误悬停 / 悬停停稳才展开
        self._anim = QPropertyAnimation(self, b"geometry")
        self._anim.setDuration(180)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.finished.connect(self._lock_size)
        self._lock_ok = True      # 只有动画自然结束才锁几何;stop() 中断时置 False 跳过
        self._target_rect = None  # 当前动画的目标矩形,_lock_size 按它强制归位
        self._skip_enter = False  # 吸附把窗口带到光标下,时效内的 enterEvent 不算悬停
        self._skip_enter_timer = QTimer(self)
        self._skip_enter_timer.setSingleShot(True)
        self._skip_enter_timer.setInterval(500)
        self._skip_enter_timer.timeout.connect(self._clear_skip)
        self._enter_timer = QTimer(self)
        self._enter_timer.setSingleShot(True)
        self._enter_timer.setInterval(120)  # 悬停去抖:光标停稳才展开
        self._enter_timer.timeout.connect(self._enter_expand)
        self._shrink_timer = QTimer(self)
        self._shrink_timer.setSingleShot(True)
        self._shrink_timer.setInterval(SHRINK_DELAY)
        self._shrink_timer.timeout.connect(self._auto_shrink)

        # ---- 界面:四环网格 + 信息行 ----
        self.rings = {}
        self.grid = QGridLayout()
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(2)
        for i, key in enumerate(("cpu", "gpu", "mem", "disk")):
            ring = RingWidget(key.upper())
            # 纯显示控件:鼠标穿透,否则按在环上时 Qt 把拖动事件抓给子控件,窗口无法拖动
            ring.setAttribute(Qt.WA_TransparentForMouseEvents)
            ring.setFixedSize(RING, RING_H)
            self.rings[key] = ring

        self.info_label = QLabel("↑ 0 KB/s   ↓ 0 KB/s\n读 0 MB/s   写 0 MB/s")
        self.info_label.setAlignment(Qt.AlignCenter)
        self.info_label.setAttribute(Qt.WA_TransparentForMouseEvents)

        self.vlay = QVBoxLayout(self)
        self.vlay.setSizeConstraint(QLayout.SetNoConstraint)  # 布局不得干预窗口最小尺寸:
        # 信息行的最小宽度提示(~76px)曾把 compact 34px 宽的窗口强行撑宽,导致贴边后探出屏幕
        self.vlay.setContentsMargins(2, 2, 2, 8)
        self.vlay.setSpacing(2)
        # 环网格固定按自身尺寸居中:信息行再宽也只撑窗口,不把两列环推开
        self._grid_host = QWidget()
        self._grid_host.setLayout(self.grid)
        self.vlay.addWidget(self._grid_host, 0, Qt.AlignHCenter)
        self.vlay.addWidget(self.info_label)
        self.info_label.setMinimumWidth(0)  # 双保险:文本宽度提示不参与窗口最小尺寸

        self._apply_layout()

    # ---- 布局与停靠状态 ----
    def set_layout(self, mode, emit=True):
        if mode not in MODES:
            return
        self.mode = mode
        self._dock_state = None
        self.dock_edge = None
        self._apply_layout()
        if emit:
            self.layout_changed.emit(mode)

    def apply_dock(self, edge):
        """恢复停靠状态(启动时用):直接以收缩小环出现。edge: top/bottom/left/right/None"""
        if edge in (None, "None", ""):
            self.dock_edge = None
            self._dock_state = None
            self._apply_layout()
            return
        self.dock_edge = edge
        self._dock_state = "compact"
        self._apply_layout()
        self._snap_to_edge(edge)

    def _apply_layout(self):
        """按 (dock_edge, _dock_state, mode) 应用内部布局(尺寸/显隐/文本)。"""
        while self.grid.count():
            self.grid.takeAt(0)
        edge = self.dock_edge
        docked = edge is not None
        state = self._dock_state if docked else None
        horizontal = edge in ("top", "bottom") if docked else None

        if docked and state == "compact":
            rows, cols = (1, 4) if horizontal else (4, 1)
            rw, rh, compact = COMPACT, COMPACT, True
        elif docked:  # full 贴边完整
            rows, cols = (1, 4) if horizontal else (4, 1)
            rw, rh, compact = RING, RING_H, False
        else:
            rows, cols = {"grid2": (2, 2), "row4": (1, 4), "col4": (4, 1)}[self.mode]
            rw, rh, compact = RING, RING_H, False

        for i, ring in enumerate(self.rings.values()):
            ring.set_compact(compact)
            ring.setFixedSize(rw, rh)
            self.grid.addWidget(ring, i // cols, i % cols, Qt.AlignCenter)

        show_text = (not docked) or (state == "full")
        self.info_label.setVisible(show_text)
        if docked:
            self.vlay.setContentsMargins(4, 4, 4, 4)
        else:
            self.vlay.setContentsMargins(2, 2, 2, 8)
        if show_text and getattr(self, "_snap", None) is None:
            # 无数据占位:与 _refresh_info 同步按形状分行,行数错了 sizeHint 会算错窗口尺寸
            vertical = edge in ("left", "right") if docked else self.mode == "col4"
            two_line = (not docked and self.mode == "grid2") or (docked and vertical)
            if vertical or (not docked and self.mode == "col4"):
                self.info_label.setText("↑ 0 KB/s\n↓ 0 KB/s\n读 0 MB/s\n写 0 MB/s")
            elif two_line:
                self.info_label.setText("↑ 0 KB/s   ↓ 0 KB/s\n读 0 MB/s   写 0 MB/s")
            else:
                self.info_label.setText("↑ 0 KB/s   ↓ 0 KB/s   读 0 MB/s   写 0 MB/s")
        self._refresh_info()

        sp = 2
        label_h = self.info_label.sizeHint().height() if show_text else 0
        fm = self.info_label.fontMetrics()
        text_w = max((fm.horizontalAdvance(t) for t in self.info_label.text().split('\n')),
                     default=0) if show_text else 0
        mh = 4 if docked else 2
        vh = (8 if docked else 10) + (2 if show_text else 0)  # margins + 网格与信息行间距
        if docked and state == "compact":
            gw = (cols * COMPACT + (cols - 1) * sp) if horizontal else COMPACT
            gh = COMPACT if horizontal else rows * COMPACT + (rows - 1) * sp
        elif docked:  # full 贴边
            gw = (cols * RING + (cols - 1) * sp) if horizontal else RING
            gh = RING_H if horizontal else rows * RING_H + (rows - 1) * sp
        else:
            gw = cols * rw + (cols - 1) * sp
            gh = rows * rh + (rows - 1) * sp
        self._grid_host.setFixedSize(gw, gh)
        w = max(gw, text_w) + 2 * mh
        h = gh + label_h + vh
        self.setMinimumSize(0, 0)
        self.setMaximumSize(16777215, 16777215)
        self.setFixedSize(w, h)

    def _refresh_info(self):
        """按当前形状刷新信息行:竖排四项各一行,2x2 分两行,其余单行。"""
        s = getattr(self, "_snap", None)
        if s is None:
            return
        if self.dock_edge is not None:
            vertical = self.dock_edge in ("left", "right")
            grid2 = False
        else:
            vertical = self.dock_edge is None and self.mode == "col4"
            grid2 = self.dock_edge is None and self.mode == "grid2"
        if vertical:
            self.info_label.setText(
                f"↑ {fmt_speed(s.net_up)}\n↓ {fmt_speed(s.net_down)}\n"
                f"读 {s.disk_read:.2f} MB/s\n写 {s.disk_write:.2f} MB/s")
        elif grid2:
            self.info_label.setText(f"↑ {fmt_speed(s.net_up)}   ↓ {fmt_speed(s.net_down)}\n"
                                    f"读 {s.disk_read:.2f} MB/s   写 {s.disk_write:.2f} MB/s")
        else:
            self.info_label.setText(f"↑ {fmt_speed(s.net_up)}   ↓ {fmt_speed(s.net_down)}   "
                                    f"读 {s.disk_read:.2f} MB/s   写 {s.disk_write:.2f} MB/s")
        if self.dock_edge is None:
            # 仅自由态按文本加宽;停靠态尺寸固定,否则每秒快照都会把贴边小环窗口撑大
            fm = self.info_label.fontMetrics()
            need = max(fm.horizontalAdvance(t) for t in self.info_label.text().split('\n')) + 10
            if need > self.width():
                self.setFixedSize(need, self.height())  # 速度变大撑破窗口时加宽,只扩不缩

    def _screen(self):
        """窗口中心按坐标直接映射所在屏幕。跨屏/跨 DPI 时窗口尺寸会突变,
        self.screen() 基于窗口状态的判定可能翻转到相邻屏,坐标映射更稳定。"""
        s = QGuiApplication.screenAt(self.frameGeometry().center())
        return s or self.screen()

    def _screen_at(self, gpos):
        s = QGuiApplication.screenAt(gpos)
        return s or self._screen()

    def _near_edge(self):
        screen = self._screen().geometry()  # 触发带覆盖全屏边缘(含任务栏区域)
        g = self.frameGeometry()
        if g.top() <= screen.top() + EDGE_SNAP:
            return "top"
        if g.bottom() >= screen.bottom() - EDGE_SNAP:
            return "bottom"
        if g.left() <= screen.left() + EDGE_SNAP:
            return "left"
        if g.right() >= screen.right() - EDGE_SNAP:
            return "right"
        return None

    def _snap_to_edge(self, edge):
        # 停靠基准用可用区域(自动排除任务栏):贴边吸附紧贴任务栏上沿而不遮挡它,
        # 避免与任务栏(同为置顶窗口)的层级冲突——点击任务栏不会再把悬浮窗压下去
        screen = self._screen().availableGeometry()
        x, y = self.x(), self.y()
        if edge == "top":
            y = screen.top() + DOCK_GAP
            x = max(screen.left() + DOCK_GAP, min(x, screen.right() - self.width() - DOCK_GAP))
        elif edge == "bottom":
            y = screen.bottom() - self.height() - DOCK_GAP
            x = max(screen.left() + DOCK_GAP, min(x, screen.right() - self.width() - DOCK_GAP))
        elif edge == "left":
            x = screen.left() + DOCK_GAP
            y = max(screen.top() + DOCK_GAP, min(y, screen.bottom() - self.height() - DOCK_GAP))
        elif edge == "right":
            x = screen.right() - self.width() - DOCK_GAP
            y = max(screen.top() + DOCK_GAP, min(y, screen.bottom() - self.height() - DOCK_GAP))
        self.move(x, y)

    # ---- 停靠悬停展开/收缩 ----
    def _dock_rect(self, edge, state, keep_center, screen=None):
        """停靠目标矩形(基于可用区域,排除任务栏)。keep_center: 平行轴锚点。"""
        av = (screen or self._screen()).availableGeometry()
        sp = 2
        horizontal = edge in ("top", "bottom")
        if state == "compact":
            if horizontal:
                w = 4 * COMPACT + 3 * sp + 8
                h = COMPACT + 8
            else:
                w = COMPACT + 8
                h = 4 * COMPACT + 3 * sp + 8
        else:
            if horizontal:
                w = 4 * RING + 3 * sp + 8
                grid_h = RING_H
            else:
                w = RING + 8
                grid_h = 4 * RING_H + 3 * sp
            label_h = self.info_label.sizeHint().height()
            w = max(w, self.info_label.sizeHint().width() + 8)
            h = grid_h + label_h + 10
        x = max(av.left() + DOCK_GAP, min(keep_center.x() - w // 2, av.right() - w - DOCK_GAP))
        y = max(av.top() + DOCK_GAP, min(keep_center.y() - h // 2, av.bottom() - h - DOCK_GAP))
        if edge == "right":
            x = av.right() - w - DOCK_GAP
        elif edge == "left":
            x = av.left() + DOCK_GAP
        elif edge == "bottom":
            y = av.bottom() - h - DOCK_GAP
        elif edge == "top":
            y = av.top() + DOCK_GAP
        return QRect(x, y, w, h)

    def _morph_to(self, state):
        """在 full/compact 停靠形态间平滑过渡(几何动画)。"""
        anchor = self.frameGeometry().center()
        scr = self._screen()
        self._dock_state = state
        self._apply_layout()  # 尺寸与信息行文本先就位,矩形按新形态计算
        rect = self._dock_rect(self.dock_edge, state, anchor, scr)
        self._animate_to(rect)

    def _animate_to(self, rect):
        self.setMinimumSize(0, 0)
        self.setMaximumSize(16777215, 16777215)
        self._target_rect = QRect(rect)
        dlog(f"animate {o_wh(self)} -> {r_str(rect)}")
        # stop() 会同步 emit finished → _lock_size,若不禁止会把动画中间尺寸
        # setFixedSize 锁死,之后新动画的 resize 全被钳住,窗口卡在错误尺寸
        self._lock_ok = False
        self._anim.stop()
        self._lock_ok = True
        self._anim.setStartValue(self.geometry())
        self._anim.setEndValue(rect)
        self._anim.start()

    def _lock_size(self):
        if not self._lock_ok or not self._dock_state or self._target_rect is None:
            return
        r = self._target_rect  # 锁定理论目标几何,动画中间的任何偏差到此清零
        self.setFixedSize(r.width(), r.height())
        self.move(r.x(), r.y())
        av = self._screen().availableGeometry()
        x = max(av.left(), min(self.x(), av.right() - self.width()))
        y = max(av.top(), min(self.y(), av.bottom() - self.height()))
        if (x, y) != (self.x(), self.y()):
            dlog(f"lock corrected {o_wh(self)} -> ({x},{y}) av={r_str(av)}")
            self.move(x, y)
        dlog(f"locked {o_wh(self)} target={r_str(r)}")
        for delay in (300, 1000):  # 事后自检:系统是否在锁定后又改动了几何
            QTimer.singleShot(delay, lambda t=QRect(r): self._verify_dock(t))

    def _verify_dock(self, r):
        if not self._dock_state:
            return
        ok = (self.x(), self.y(), self.width(), self.height()) == (r.x(), r.y(), r.width(), r.height())
        dlog(f"verify {o_wh(self)} frame={o_wh(self.frameGeometry())} target={r_str(r)} "
             f"{'OK' if ok else 'MISMATCH'} visible={not self.visibleRegion().isEmpty()}")
        try:
            p = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
            self.grab().save(str(p / "dock_snap.png"))
        except Exception:
            pass

    if _WIN32:
        # 条件定义:仅 Windows 存在此原生消息,Linux/macOS 打包时也不引入 ctypes 结构
        def nativeEvent(self, eventType, message):
            # Windows 把窗口最小宽度钳到系统值(本机 76px),吸附小环 34px 宽会被撑破;
            # 改写 MINMAXINFO 最小跟踪尺寸为 1,窗口尺寸完全由 setFixedSize 决定
            if eventType == "windows_generic_MSG" \
                    and wintypes.MSG.from_address(int(message)).message == _WM_GETMINMAXINFO:
                mmi = _MINMAXINFO.from_address(wintypes.MSG.from_address(int(message)).lParam)
                mmi.ptMinTrackSize.x = 1
                mmi.ptMinTrackSize.y = 1
                return True, 0
            return super().nativeEvent(eventType, message)

    def _clear_skip(self):
        self._skip_enter = False

    def _enter_expand(self):
        if self.underMouse() and self.dock_edge and self._dock_state == "compact":
            self._morph_to("full")

    def enterEvent(self, _):
        # 松手吸附把窗口带到光标下,时效内的进入都不是用户主动悬停,跳过展开
        if self._skip_enter:
            return
        # 悬停收缩小环 → 去抖后平滑展开完整数据(光标扫过不展开)
        if self.dock_edge and self._dock_state == "compact" and not self._dragging:
            self._enter_timer.start()

    def leaveEvent(self, _):
        self._enter_timer.stop()  # 离开即取消未定的展开
        # 展开态鼠标离开 → 延时收起
        if self.dock_edge and self._dock_state == "full" and not self._dragging:
            self._shrink_timer.start()

    def _auto_shrink(self):
        if self._dragging or self.underMouse():
            return  # 拖动中/鼠标仍在窗口上:保持完整
        if self.dock_edge and self._dock_state == "full":
            self._morph_to("compact")

    # ---- 半透明圆角背景 ----
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(pal()["panel"])
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 12, 12)
        p.end()

    def refresh_theme(self):
        c = pal()["dim"]
        style = f"color: rgba({c.red()},{c.green()},{c.blue()},{c.alpha()}); font-size: 11px;"
        self.info_label.setStyleSheet(style)
        self.update()

    # ---- 拖动(轮询驱动):拖动全程自由跟手,松手瞬间才判定吸附 ----
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._lock_ok = False
            self._anim.stop()  # 动画与拖动互斥
            self._lock_ok = True
            self._shrink_timer.stop()
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._press_gpos = e.globalPosition().toPoint()
            self._dragging = False
            self._poll.start()

    def mouseMoveEvent(self, e):
        if self._drag:
            self._handle_drag(e.globalPosition().toPoint())

    def _poll_drag(self):
        if not self._drag:
            self._poll.stop()
            return
        self._handle_drag(QCursor.pos())

    def mouseReleaseEvent(self, e):
        dragging = self._dragging
        gpos = e.globalPosition().toPoint()
        self._drag = None
        self._dragging = False
        self._poll.stop()
        if not dragging:
            return  # 原地点击:交给悬停展开/收起
        # 松手吸附:窗口任一边贴近屏幕边缘 ≤ EDGE_SNAP 时,平滑收缩吸附到该边。
        # 先按松手时的稳定状态算好目标矩形,再改变窗口尺寸——顺序反了的话,
        # 尺寸突变会移动窗口中心,跨屏时 screen() 翻转到相邻屏,吸附坐标就错了
        edge = self._near_edge()
        if edge:
            scr = self._screen_at(gpos)  # 以松手点锚定屏幕,窗口尺寸突变也不漂移
            self.dock_edge = edge
            self._dock_state = "compact"
            self._apply_layout()
            rect = self._dock_rect(edge, "compact", gpos, scr)
            self._animate_to(rect)
            self._skip_enter = True  # 吸附后窗口滑到光标下,不得立即触发悬停展开
            self._skip_enter_timer.start()
            self._enter_timer.stop()
            dlog(f"dock edge={edge} gpos=({gpos.x()},{gpos.y()}) "
                 f"win={o_wh(self)} -> rect={r_str(rect)} scr={scr.geometry()} avail={scr.availableGeometry()} "
                 f"dpr={scr.devicePixelRatio()}")

    def _handle_drag(self, gpos):
        try:
            self._do_drag(gpos)
        except Exception:
            try:
                p = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
                with open(p / "drag_error.txt", "a", encoding="utf-8") as f:
                    import traceback
                    f.write(time.strftime("%H:%M:%S ") + traceback.format_exc())
            except Exception:
                pass

    def _do_drag(self, gpos):
        if not self._dragging:
            # 按压死区 10px:区分"原地点击"与"拖动"
            if (abs(gpos.x() - self._press_gpos.x())
                    + abs(gpos.y() - self._press_gpos.y())) < 10:
                return
            self._dragging = True
            if self.dock_edge:  # 开始拖动:脱离停靠,恢复完整布局跟手
                self.dock_edge = None
                self._dock_state = None
                self._apply_layout()
                self._drag = gpos - self.frameGeometry().topLeft()
        self.move(gpos - self._drag)  # 自由跟手,无吸附干扰
        self._clamp_inside()

    def _clamp_inside(self):
        """窗口约束在虚拟桌面(全部屏幕的联合范围)内:允许跨屏拖动,
        每块屏幕的边缘因此都能触发吸附;只挡住完全丢出桌面的情况。"""
        v = self.screen().virtualGeometry()
        x = max(v.left(), min(self.x(), v.right() - self.width()))
        y = max(v.top(), min(self.y(), v.bottom() - self.height()))
        if (x, y) != (self.x(), self.y()):
            self.move(x, y)

    # ---- 双击 / 右键 ----
    def mouseDoubleClickEvent(self, _):
        self.open_main.emit()

    def contextMenuEvent(self, e):
        menu = QMenu(self)
        menu.addAction("打开主窗口", self.open_main.emit)
        lay_menu = menu.addMenu("布局")
        for mode, label in (("grid2", "2x2 网格"), ("row4", "横排 4x1"), ("col4", "竖排 1x4")):
            act = lay_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(self.mode == mode)
            act.triggered.connect(lambda _, m=mode: self.set_layout(m))
        menu.addSeparator()
        menu.addAction("释放内存", self.release_memory.emit)
        menu.addAction("退出", self.quit_app.emit)
        menu.exec(e.globalPos())

    # ---- 数据 ----
    def apply_settings(self, warn, danger):
        for ring in self.rings.values():
            ring.set_thresholds(warn, danger)

    def update_snapshot(self, snap):
        self._snap = snap
        self._refresh_info()
        temps = {"cpu": snap.temp, "gpu": snap.gpu_temp,
                 "mem": snap.mem_temp, "disk": snap.disk_temp}
        for key, val in (("cpu", snap.cpu), ("gpu", snap.gpu),
                         ("mem", snap.mem), ("disk", snap.disk)):
            ring = self.rings[key]
            if self.temp_mode:  # 温度模式:各环显示对应温度
                ring.set_unit("°")
                ring.set_value(None if temps[key] is None else float(temps[key]))
            else:
                ring.set_unit("")
                ring.set_value(None if val is None else float(val))
