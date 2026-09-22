"""桌面悬浮窗:无边框、置顶、可拖动、靠边吸附停靠;2x2 / 4x1 / 1x4 布局。

停靠(贴屏边)时进入紧凑模式:每个指标只剩一个小环,无数字文字;
贴上下边横排 4x1,贴左右边竖排 1x4;拖离边缘恢复完整显示。
"""
from PySide6.QtCore import QEvent, QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QCursor, QPainter
from PySide6.QtWidgets import QGridLayout, QLabel, QMenu, QVBoxLayout, QWidget

from widgets import RingWidget, pal

RING, RING_H = 66, 80    # 正常态环单元
COMPACT = 26             # 停靠态环尺寸
EDGE_SNAP = 40           # 吸附触发距离(px)
UNDOCK_DIST = 60         # 停靠后鼠标向屏幕内侧拉离该距离才解除(相对吸附时刻,沿边滑动不解除)
DOCK_GAP = 4             # 停靠时距屏幕边缘
MODES = ("grid2", "row4", "col4")


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

        self.mode = "grid2"     # 正常态布局
        self.dock_edge = None   # None / top / bottom / left / right
        self._expanded = False  # 停靠悬停展开
        self.temp_mode = False  # 温度显示模式:环显示温度而非百分比
        self._drag = None
        self._no_snap = False   # 本次拖动已解除停靠:松手前不再吸附(根除抖动)
        self._poll = QTimer(self)  # 拖动轮询:窗口尺寸突变后 Qt 事件可能丢失,轮询免疫
        self._poll.setInterval(16)
        self._poll.timeout.connect(self._poll_drag)

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

        self.info_label = QLabel("↑ 0 KB/s   ↓ 0 KB/s   读 0 MB/s   写 0 MB/s")
        self.info_label.setAlignment(Qt.AlignCenter)
        self.info_label.setAttribute(Qt.WA_TransparentForMouseEvents)

        self.vlay = QVBoxLayout(self)
        self.vlay.setContentsMargins(2, 2, 2, 8)
        self.vlay.setSpacing(2)
        self.vlay.addLayout(self.grid)
        self.vlay.addWidget(self.info_label)

        self._relayout()

    # ---- 布局与停靠 ----
    def set_layout(self, mode, emit=True):
        if mode not in MODES:
            return
        self.mode = mode
        self._expanded = False
        self.dock_edge = None
        self._dock_mouse = None
        self._relayout()
        if emit:
            self.layout_changed.emit(mode)

    def apply_dock(self, edge):
        """恢复停靠状态(启动时用)。edge: top/bottom/left/right/None"""
        self._expanded = False
        if edge in (None, "None", ""):
            self.dock_edge = None
            self._dock_mouse = None
            self._relayout()
            return
        self.dock_edge = edge
        self._dock_mouse = QCursor.pos()  # 解除基准:当前鼠标位置
        self._relayout()
        self._snap_to_edge(edge)

    def _refresh_info(self):
        """按当前形状刷新信息行:竖排四项各一行,2x2 分两行,其余单行。"""
        s = getattr(self, "_snap", None)
        if s is None:
            return
        if self.dock_edge is not None:
            vertical = self.dock_edge in ("left", "right")
            grid2 = False
        else:
            vertical = self.mode == "col4"
            grid2 = self.mode == "grid2"
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

    def _relayout(self):
        while self.grid.count():
            self.grid.takeAt(0)
        docked = self.dock_edge is not None
        horizontal = self.dock_edge in ("top", "bottom") if docked else None
        expanded = docked and self._expanded

        if expanded:
            rows, cols = (1, 4) if horizontal else (4, 1)
            ring_w, ring_h, compact = RING, RING_H, False
        elif docked:
            rows, cols = (1, 4) if horizontal else (4, 1)
            ring_w, ring_h, compact = COMPACT, COMPACT, True
        else:
            rows, cols = {"grid2": (2, 2), "row4": (1, 4), "col4": (4, 1)}[self.mode]
            ring_w, ring_h, compact = RING, RING_H, False

        for i, ring in enumerate(self.rings.values()):
            ring.set_compact(compact)
            ring.setFixedSize(ring_w, ring_h)
            self.grid.addWidget(ring, i // cols, i % cols, Qt.AlignCenter)

        show_text = not docked or expanded
        self.info_label.setVisible(show_text)
        if docked and not expanded:
            self.vlay.setContentsMargins(4, 4, 4, 4)
        else:
            self.vlay.setContentsMargins(2, 2, 2, 8 if not docked else 4)
        self._refresh_info()

        sp = 2
        label_h = self.info_label.sizeHint().height() if show_text else 0
        if docked and not expanded:
            m = 4
            if horizontal:
                w = cols * COMPACT + (cols - 1) * sp + 2 * m
                h = COMPACT + 2 * m
            else:
                w = COMPACT + 2 * m
                h = rows * COMPACT + (rows - 1) * sp + 2 * m
        else:
            w = cols * ring_w + (cols - 1) * sp + 4
            grid_h = rows * ring_h + (rows - 1) * sp
            h = grid_h + label_h + (6 if expanded else 10)
            # 窄布局(竖排/2x2):宽度以一行信息文字为准
            w = max(w, self.info_label.sizeHint().width() + 6)
        self.setFixedSize(w, h)

    def _near_edge(self):
        screen = self.screen().geometry()  # 含任务栏,可停靠其上
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

    def _pull_out(self, gpos):
        """解除判定:鼠标从吸附时刻的位置向屏幕内侧拉离超过阈值才解除;
        沿边滑动(平行方向)永不误触,吸附瞬间也不会因手抖立即弹开。"""
        a = getattr(self, "_dock_mouse", None)
        if a is None:
            return False
        if self.dock_edge == "right":
            return gpos.x() < a.x() - UNDOCK_DIST
        if self.dock_edge == "left":
            return gpos.x() > a.x() + UNDOCK_DIST
        if self.dock_edge == "top":
            return gpos.y() > a.y() + UNDOCK_DIST
        return gpos.y() < a.y() - UNDOCK_DIST

    def _snap_to_edge(self, edge):
        screen = self.screen().geometry()
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

    # ---- 停靠悬停展开 ----
    def _expand(self, expand):
        """停靠态展开(显示数据)/收起(只留小环),保持贴边并按原中心对齐。"""
        center = self.frameGeometry().center()
        self._expanded = expand
        self._relayout()
        screen = self.screen().geometry()
        gap = DOCK_GAP
        x, y = self.x(), self.y()
        if self.dock_edge == "top":
            y = screen.top() + gap
            x = max(screen.left() + gap, min(center.x() - self.width() // 2,
                    screen.right() - self.width() - gap))
        elif self.dock_edge == "bottom":
            y = screen.bottom() - self.height() - gap
            x = max(screen.left() + gap, min(center.x() - self.width() // 2,
                    screen.right() - self.width() - gap))
        elif self.dock_edge == "left":
            x = screen.left() + gap
            y = max(screen.top() + gap, min(center.y() - self.height() // 2,
                    screen.bottom() - self.height() - gap))
        elif self.dock_edge == "right":
            x = screen.right() - self.width() - gap
            y = max(screen.top() + gap, min(center.y() - self.height() // 2,
                    screen.bottom() - self.height() - gap))
        self.move(x, y)

    def changeEvent(self, e):
        # Win+D/"显示桌面"会把 Qt.Tool 悬浮窗最小化且永不自动恢复,立即还原
        if e.type() == QEvent.Type.WindowStateChange and self.windowState() & Qt.WindowMinimized:
            self.setWindowState(Qt.WindowNoState)
            self.show()
            self.raise_()
        super().changeEvent(e)

    def enterEvent(self, _):
        if self.dock_edge and not self._expanded:
            self._expand(True)

    def leaveEvent(self, _):
        if self.dock_edge and self._expanded:
            self._expand(False)

    # ---- 拖动 / 双击 / 右键 ----
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._poll.start()

    def mouseMoveEvent(self, e):
        if self._drag:  # 常规路径(轮询为主,事件兜底)
            self._handle_drag(e.globalPosition().toPoint())

    def _poll_drag(self):
        if not self._drag:
            self._poll.stop()
            return
        self._handle_drag(QCursor.pos())

    def _handle_drag(self, gpos):
        screen = self.screen().geometry()
        try:
            self._do_drag(gpos, screen)
        except Exception:
            import time as _t
            import traceback
            try:
                with open("drag_error.txt", "a", encoding="utf-8") as f:
                    f.write(_t.strftime("%H:%M:%S ") + traceback.format_exc() + "\n")
            except Exception:
                pass

    def _do_drag(self, gpos, screen):
        if self.dock_edge:  # 停靠态:沿边滑动,向屏幕内侧拉离才解除(防抖)
            if self._pull_out(gpos):
                self.dock_edge = None
                self._expanded = False
                self._dock_mouse = None
                self._no_snap = True  # 本次拖动不再吸附,松手后恢复
                self._relayout()
                self.move(gpos.x(), gpos.y())  # 窗口摆在鼠标右下方,不再压住屏幕边缘
                self._clamp_inside(screen)
                self._drag = gpos - self.frameGeometry().topLeft()
                return
            gap = DOCK_GAP
            if self.dock_edge in ("top", "bottom"):
                y = (screen.top() + gap if self.dock_edge == "top"
                     else screen.bottom() - self.height() - gap)
                x = max(screen.left() + gap, min(gpos.x() - self._drag.x(),
                        screen.right() - self.width() - gap))
            else:
                x = (screen.left() + gap if self.dock_edge == "left"
                     else screen.right() - self.width() - gap)
                y = max(screen.top() + gap, min(gpos.y() - self._drag.y(),
                        screen.bottom() - self.height() - gap))
            self.move(x, y)
            return

        self.move(gpos - self._drag)  # 自由拖动
        self._clamp_inside(screen)  # 窗口完整留在屏幕内,四个环都可见
        edge = None if self._no_snap else self._near_edge()  # 解除过就不再吸附,直至松手
        if not edge:
            return
        self.dock_edge = edge
        self._dock_mouse = QPoint(gpos)  # 记录吸附时刻鼠标位置,作为解除基准
        self._relayout()
        self._drag = gpos - self.frameGeometry().topLeft()
        self._snap_to_edge(edge)

    def _clamp_inside(self, screen):
        """窗口完整约束在屏幕内(不部分出屏,保证四个环都可见)。"""
        x = max(screen.left(), min(self.x(), screen.right() - self.width()))
        y = max(screen.top(), min(self.y(), screen.bottom() - self.height()))
        if (x, y) != (self.x(), self.y()):
            self.move(x, y)

    def mouseReleaseEvent(self, _):
        self._drag = None
        self._no_snap = False  # 松手恢复吸附能力
        self._poll.stop()
        self._poll.stop()

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
