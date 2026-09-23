"""圆环仪表(手机电量风格,阈值变色;停靠收缩态环心带硬件线稿小图标)与折线小图。"""
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication, QWidget

GREEN = QColor("#4cd97b")
YELLOW = QColor("#f5c542")
RED = QColor("#f0554d")

# 亮/暗调色板(半透明灰阶,iPhone 质感)
PALETTES = {
    "dark": dict(
        text=QColor(242, 242, 247),
        dim=QColor(235, 235, 245, 150),
        track=QColor(120, 120, 128, 85),
        card=QColor(120, 120, 128, 60),
        panel=QColor(24, 24, 27, 205),
    ),
    "light": dict(
        text=QColor(28, 28, 30),
        dim=QColor(60, 60, 67, 145),
        track=QColor(120, 120, 128, 55),
        card=QColor(120, 120, 128, 38),
        panel=QColor(248, 248, 250, 185),
    ),
}
_theme = "dark"


def set_theme(key):
    global _theme
    if key in PALETTES:
        _theme = key
        for w in QApplication.allWidgets():
            w.update()


def pal():
    return PALETTES[_theme]


def level_color(value, warn, danger):
    if value >= danger:
        return RED
    if value >= warn:
        return YELLOW
    return GREEN


class RingWidget(QWidget):
    """圆环百分比:value=None 时显示 '--'。"""

    def __init__(self, label, parent=None):
        super().__init__(parent)
        self.label = label
        self.value = None
        self.unit = ""  # 数值单位后缀(温度模式为 '°')
        self.sub = ""  # 环内第二行小字(可选)
        self.compact = False  # 停靠紧凑模式:只画环,无文字
        self.warn, self.danger = 80, 90

    def set_value(self, v):
        self.value = v
        self.update()

    def set_unit(self, u):
        self.unit = u
        self.update()

    def set_compact(self, c):
        self.compact = c
        self.update()

    def set_sub(self, s):
        self.sub = s
        self.update()

    def set_thresholds(self, warn, danger):
        self.warn, self.danger = warn, danger
        self.update()

    def _draw_device_icon(self, p, c, cx, cy, s):
        """收缩小环中心的硬件线稿图标(极简线条风,对应 CPU/GPU/MEM/DISK)。s=外接边长。"""
        pen = QPen(c["dim"], max(1.0, s * 0.10), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        h = s / 2
        kind = self.label.upper()

        if kind == "CPU":  # 芯片:方身 + 中心核 + 四边引脚
            b = h * 0.62
            p.drawRoundedRect(QRectF(cx - b, cy - b, b * 2, b * 2), b * 0.3, b * 0.3)
            p.drawEllipse(QPointF(cx, cy), b * 0.36, b * 0.36)
            for t in (-b * 0.42, b * 0.42):
                p.drawLine(QPointF(cx + t, cy - b), QPointF(cx + t, cy - h))
                p.drawLine(QPointF(cx + t, cy + b), QPointF(cx + t, cy + h))
                p.drawLine(QPointF(cx - b, cy + t), QPointF(cx - h, cy + t))
                p.drawLine(QPointF(cx + b, cy + t), QPointF(cx + h, cy + t))

        elif kind == "GPU":  # 显卡:卡身 + 风扇 + 尾部散热孔
            p.drawRoundedRect(QRectF(cx - h, cy - h * 0.52, s, h * 1.04), s * 0.1, s * 0.1)
            fx = cx - h * 0.3
            p.drawEllipse(QPointF(fx, cy), h * 0.32, h * 0.32)
            p.drawEllipse(QPointF(fx, cy), h * 0.08, h * 0.08)
            for vx in (cx + h * 0.42, cx + h * 0.62):
                p.drawLine(QPointF(vx, cy - h * 0.26), QPointF(vx, cy + h * 0.26))

        elif kind == "MEM":  # 内存:竖条 + 两颗颗粒 + 底部金手指
            w2, bh = h * 0.36, h * 0.76
            p.drawRoundedRect(QRectF(cx - w2, cy - bh, w2 * 2, bh * 2), w2 * 0.4, w2 * 0.4)
            for yy in (cy - bh * 0.42, cy + bh * 0.12):
                p.drawRect(QRectF(cx - w2 * 0.55, yy, w2 * 1.1, bh * 0.36))
            for i in range(3):
                xx = cx - w2 * 0.5 + i * w2 * 0.5
                p.drawLine(QPointF(xx, cy + bh), QPointF(xx, cy + h))

        else:  # DISK 硬盘:方身 + 盘片 + 主轴 + 摇臂
            p.drawRoundedRect(QRectF(cx - h, cy - h * 0.8, s, h * 1.6), s * 0.12, s * 0.12)
            px, py = cx - h * 0.18, cy + h * 0.06
            p.drawEllipse(QPointF(px, py), h * 0.38, h * 0.38)
            p.setBrush(pen.color())
            p.drawEllipse(QPointF(px, py), h * 0.09, h * 0.09)
            p.setBrush(Qt.NoBrush)
            p.drawLine(QPointF(cx + h * 0.55, cy - h * 0.5), QPointF(px + h * 0.26, py - h * 0.2))

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = pal()

        if self.compact:  # 停靠态:占满单元的小环 + 环心硬件小图标
            w = min(self.width(), self.height())
            pen_w = max(3.0, w * 0.16)
            d = w - pen_w - 3
            rect = QRectF((self.width() - d) / 2, (self.height() - d) / 2, d, d)
            pen = QPen(c["track"], pen_w, Qt.SolidLine, Qt.RoundCap)
            p.setPen(pen)
            p.drawArc(rect, 0, 360 * 16)
            if self.value is not None:
                pen.setColor(level_color(self.value, self.warn, self.danger))
                p.setPen(pen)
                p.drawArc(rect, 90 * 16, -int(self.value * 3.6) * 16)
            self._draw_device_icon(p, c, rect.center().x(), rect.center().y(), d * 0.62)
            p.end()
            return

        # 上方环形区 + 下方独立标签区;环在环形区内垂直居中,间隙均匀
        label_h = max(15, int(self.height() * 0.15))
        avail_h = self.height() - label_h
        w = min(self.width(), avail_h)
        pen_w = max(5.0, w * 0.11)
        d = w - pen_w - 6  # 直径,给笔宽外缘留白
        rect = QRectF((self.width() - d) / 2, (avail_h - d) / 2, d, d)

        pen = QPen(c["track"], pen_w, Qt.SolidLine, Qt.RoundCap)
        p.setPen(pen)
        p.drawArc(rect, 0, 360 * 16)

        if self.value is not None:
            pen.setColor(level_color(self.value, self.warn, self.danger))
            p.setPen(pen)
            # 12 点方向起始,顺时针
            p.drawArc(rect, 90 * 16, -int(self.value * 3.6) * 16)

        # 中心文字(有副标题时上下分区)
        inner = rect.adjusted(pen_w, pen_w, -pen_w, -pen_w)
        p.setPen(c["text"])
        text = "--" if self.value is None else f"{self.value:.0f}{self.unit}"
        if self.sub:
            p.setFont(QFont("Microsoft YaHei UI", int(w * 0.17), QFont.Bold))
            p.drawText(QRectF(inner.left(), inner.top(), inner.width(), inner.height() * 0.58),
                       Qt.AlignCenter, text)
            p.setFont(QFont("Microsoft YaHei UI", max(7, int(w * 0.07))))
            p.setPen(c["dim"])
            p.drawText(QRectF(inner.left(), inner.top() + inner.height() * 0.55,
                              inner.width(), inner.height() * 0.35),
                       Qt.AlignCenter, self.sub)
        else:
            p.setFont(QFont("Microsoft YaHei UI", int(w * 0.21), QFont.Bold))
            p.drawText(inner, Qt.AlignCenter, text)

        # 底部指标名
        p.setFont(QFont("Microsoft YaHei UI", max(8, int(w * 0.085))))
        p.setPen(c["dim"])
        p.drawText(QRectF(0, self.height() - label_h, self.width(), label_h),
                   Qt.AlignHCenter | Qt.AlignVCenter, self.label)
        p.end()


class Sparkline(QWidget):
    """无坐标轴的历史折线,数据 0~100(速率类会自动按峰值缩放)。"""

    def __init__(self, color=GREEN, parent=None):
        super().__init__(parent)
        self.color = color
        self.data = []
        self.setMinimumHeight(46)

    def set_data(self, data):
        self.data = list(data) if data else []
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(pal()["card"])
        p.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 8, 8)
        if len(self.data) < 2:
            p.end()
            return

        w, h = self.width() - 8, self.height() - 8
        peak = max(100.0, *(v for v in self.data if v is not None), 0.0)
        vals = [0.0 if v is None else v for v in self.data]  # 无数据(GPU 缺失)画在底部
        step = w / (len(vals) - 1)
        pts = [QPointF(4 + i * step, 4 + h - min(1.0, v / peak) * h)
               for i, v in enumerate(vals)]

        path = QPainterPath(pts[0])
        for pt in pts[1:]:
            path.lineTo(pt)
        pen = QPen(self.color, 1.6)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)

        # 下方渐变填充
        fill = QPainterPath(path)
        fill.lineTo(pts[-1].x(), 4 + h)
        fill.lineTo(pts[0].x(), 4 + h)
        fill.closeSubpath()
        grad = QLinearGradient(0, 0, 0, self.height())
        c = QColor(self.color)
        c.setAlpha(70)
        grad.setColorAt(0, c)
        c2 = QColor(self.color)
        c2.setAlpha(0)
        grad.setColorAt(1, c2)
        p.setPen(Qt.NoPen)
        p.setBrush(grad)
        p.drawPath(fill)
        p.end()


class GaugeBox(QWidget):
    """主窗口用:一个大圆环 + 名称 + 折线的纵向组合。"""

    def __init__(self, label, parent=None):
        super().__init__(parent)
        from PySide6.QtWidgets import QVBoxLayout
        self.ring = RingWidget(label)
        self.ring.setFixedSize(124, 150)
        self.spark = Sparkline()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 0, 4, 0)
        lay.addWidget(self.ring, 0, Qt.AlignHCenter)
        lay.addWidget(self.spark)
