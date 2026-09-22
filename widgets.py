"""圆环仪表(手机电量风格,阈值变色)与折线小图。"""
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

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = pal()

        if self.compact:  # 停靠态:只画一个占满单元的小环
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
            p.end()
            return

        # 上方环形区 + 下方独立标签区,避免文字压环
        label_h = max(15, int(self.height() * 0.15))
        w = min(self.width(), self.height() - label_h)
        pen_w = max(5.0, w * 0.11)
        d = w - pen_w - 6  # 直径,给笔宽外缘留白
        rect = QRectF((self.width() - d) / 2, pen_w / 2 + 2, d, d)

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
