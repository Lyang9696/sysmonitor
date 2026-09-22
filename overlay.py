"""桌面悬浮窗:无边框、置顶、可拖动、四边吸附停靠;2x2 / 4x1 / 1x4 布局。

停靠交互(全程几何动画,无突变):
  拖近屏幕边缘 → 窗口保持原尺寸平滑贴边(不收缩)
  松手 1.5s 后 → 平滑收缩为小环;鼠标移回 → 平滑展开
  从贴边往屏幕内拖 60px → 平滑回到自由跟随状态
"""
from PySide6.QtCore import (QEasingCurve, QEvent, QPoint, QPointF,
                            QPropertyAnimation, QRect, QRectF, Qt,
                            QTimer, Signal)
from PySide6.QtGui import QCursor, QPainter
from PySide6.QtWidgets import QGridLayout, QLabel, QMenu, QVBoxLayout, QWidget

from widgets import RingWidget, pal

RING, RING_H = 66, 80    # 正常态环单元
COMPACT = 26             # 停靠收缩态环尺寸
EDGE_SNAP = 20           # 松手时窗口边缘距屏幕边缘 ≤ 该距离则吸附(px)
DOCK_GAP = 4             # 停靠时距屏幕边缘
SHRINK_DELAY = 600       # 悬停展开后鼠标离开→收起的延时(ms)
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

        self.mode = "grid2"       # 正常态布局
        self.dock_edge = None     # None / top / bottom / left / right
        self._dock_state = None   # None(自由) / "full"(贴边完整) / "compact"(收缩小环)
        self._drag = None         # 拖动偏移
        self._dragging = False    # 是否已进入拖动(超过按压死区)
        self._press_gpos = None   # 按下时鼠标全局位置(区分点击与拖动)
        self.temp_mode = False    # 温度显示模式:环显示温度而非百分比
        self._poll = QTimer(self)
        self._poll.setInterval(16)
        self._poll.timeout.connect(self._poll_drag)
        self._anim = QPropertyAnimation(self, b"geometry")
        self._anim.setDuration(180)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.finished.connect(self._lock_size)
        self._shrink_timer = QTimer(self)
        self._shrink_timer.setSingleShot(True)
        self._shrink_timer.setInterval(SHRINK_DELAY)
        self._shrink_timer.timeout.connect(self._auto_shrink)

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
        self._refresh_info()

        sp = 2
        label_h = self.info_label.sizeHint().height() if show_text else 0
        if docked and state == "compact":
            m = 4
            if horizontal:
                w = cols * COMPACT + (cols - 1) * sp + 2 * m
                h = COMPACT + 2 * m
            else:
                w = COMPACT + 2 * m
                h = rows * COMPACT + (rows - 1) * sp + 2 * m
        elif docked:  # full 贴边
            if horizontal:
                w = cols * RING + (cols - 1) * sp + 4
                grid_h = RING_H
            else:
                w = RING + 4
                grid_h = rows * RING_H + (rows - 1) * sp
            w = max(w, self.info_label.sizeHint().width() + 6)
            h = grid_h + label_h + 8
        else:
            w = cols * rw + (cols - 1) * sp + 4
            grid_h = rows * rh + (rows - 1) * sp
            h = grid_h + label_h + 10
            w = max(w, self.info_label.sizeHint().width() + 6)
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

    def _near_edge(self):
        screen = self.screen().geometry()  # 触发带覆盖全屏边缘(含任务栏区域)
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
        screen = self.screen().availableGeometry()
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
    def _dock_rect(self, edge, state, keep_center):
        """停靠目标矩形(基于可用区域,排除任务栏)。keep_center: 平行轴锚点。"""
        av = self.screen().availableGeometry()
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
            h = grid_h + label_h + 8
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
        self._dock_state = state
        self._apply_layout()
        center = self.frameGeometry().center()
        rect = self._dock_rect(self.dock_edge, state, center)
        self._animate_to(rect)

    def _animate_to(self, rect):
        self.setMinimumSize(0, 0)
        self.setMaximumSize(16777215, 16777215)
        self._anim.stop()
        self._anim.setStartValue(self.geometry())
        self._anim.setEndValue(rect)
        self._anim.start()

    def _lock_size(self):
        if self._dock_state:
            self.setFixedSize(self.width(), self.height())

    def enterEvent(self, _):
        # 悬停收缩小环 → 平滑展开完整数据
        if self.dock_edge and self._dock_state == "compact" and not self._dragging:
            self._morph_to("full")

    def leaveEvent(self, _):
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
            self._anim.stop()  # 动画与拖动互斥
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
        # 松手吸附:窗口任一边贴近屏幕边缘 ≤ EDGE_SNAP 时,平滑收缩吸附到该边
        edge = self._near_edge()
        if edge:
            self.dock_edge = edge
            self._dock_state = "compact"
            self._apply_layout()
            self._animate_to(self._dock_rect(edge, "compact", gpos))

    def _handle_drag(self, gpos):
        screen = self.screen().geometry()
        try:
            self._do_drag(gpos, screen)
        except Exception:
            import time as _t
            import traceback
            try:
                with open("drag_error.txt", "a", encoding="utf-8") as f:
                    f.write(_t.strftime("%H:%M:%S ") + traceback.format_exc())
            except Exception:
                pass

    def _do_drag(self, gpos, screen):
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
        self._clamp_inside(screen)    # 窗口完整留在屏幕内

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
