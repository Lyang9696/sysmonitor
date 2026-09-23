"""主窗口:大圆环 + 折线历史 + 设置。关闭时隐藏到托盘。"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QGridLayout, QGroupBox,
                               QHBoxLayout, QLabel, QMainWindow, QSpinBox, QVBoxLayout, QWidget)

from collector import HAS_GPU
from widgets import GaugeBox, level_color


def fmt_speed(kb):
    if kb >= 1024:
        return f"{kb / 1024:.1f} MB/s"
    return f"{kb:.0f} KB/s"


class MainWindow(QMainWindow):
    settings_changed = Signal(dict)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("系统监控")
        self.resize(760, 560)
        self._gpu_warned = not HAS_GPU

        central = QWidget(objectName="central")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(10)

        title = QLabel("系统监控")
        title.setStyleSheet("font-size: 17px; font-weight: bold;")
        self.subtitle = QLabel("" if HAS_GPU else "未检测到 GPU")
        self.subtitle.setObjectName("subtitle")
        root.addWidget(title)
        root.addWidget(self.subtitle)

        # 四个 大圆环 + 折线
        grid = QGridLayout()
        grid.setSpacing(12)
        self.boxes = {}
        for i, (key, name) in enumerate((("cpu", "CPU"), ("gpu", "GPU"),
                                         ("mem", "内存"), ("disk", "磁盘"))):
            box = GaugeBox(name)
            self.boxes[key] = box
            grid.addWidget(box, 0, i)
        root.addLayout(grid)

        # 详情行
        self.detail = QLabel()
        self.detail.setObjectName("detail")
        root.addWidget(self.detail)

        # 设置:两行网格,标签+控件成组
        group = QGroupBox("设置")
        grid2 = QGridLayout(group)
        grid2.setHorizontalSpacing(22)
        grid2.setVerticalSpacing(10)

        def spin(lo, hi, val, suffix=""):
            s = QSpinBox()
            s.setRange(lo, hi)
            s.setValue(val)
            s.setSuffix(suffix)
            s.setAlignment(Qt.AlignCenter)
            return s

        def field(label, widget):
            box = QWidget()
            h = QHBoxLayout(box)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(7)
            lab = QLabel(label)
            lab.setObjectName("fieldLabel")
            h.addWidget(lab)
            h.addWidget(widget)
            return box

        self.interval_spin = spin(1, 10, 1, " s")
        self.warn_spin = spin(50, 95, 80, " %")
        self.danger_spin = spin(51, 100, 90, " %")
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["百分比", "温度"])
        self.temp_warn_spin = spin(30, 100, 60, " °C")
        self.temp_danger_spin = spin(31, 110, 80, " °C")
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["暗色", "亮色"])
        self.overlay_check = QCheckBox("显示悬浮窗")
        self.overlay_check.setChecked(True)

        grid2.addWidget(field("刷新间隔", self.interval_spin), 0, 0)
        grid2.addWidget(field("黄色阈值", self.warn_spin), 0, 1)
        grid2.addWidget(field("红色阈值", self.danger_spin), 0, 2)
        grid2.addWidget(field("环显示", self.mode_combo), 0, 3)
        grid2.addWidget(field("温度黄阈", self.temp_warn_spin), 1, 0)
        grid2.addWidget(field("温度红阈", self.temp_danger_spin), 1, 1)
        grid2.addWidget(field("主题", self.theme_combo), 1, 2)
        grid2.addWidget(self.overlay_check, 1, 3)
        grid2.setColumnStretch(4, 1)
        root.addWidget(group)

        for s in (self.interval_spin, self.warn_spin, self.danger_spin,
                  self.temp_warn_spin, self.temp_danger_spin):
            s.valueChanged.connect(self._emit_settings)
        self.mode_combo.currentIndexChanged.connect(self._emit_settings)
        self.theme_combo.currentIndexChanged.connect(self._emit_settings)
        self.overlay_check.toggled.connect(self._emit_settings)
        self._emit_settings()  # 初始化环阈值

    def _temp_mode(self):
        return self.mode_combo.currentIndex() == 1

    def _emit_settings(self):
        cfg = {
            "interval": self.interval_spin.value(),
            "warn": self.warn_spin.value(),
            "danger": self.danger_spin.value(),
            "show_overlay": self.overlay_check.isChecked(),
            "theme": "dark" if self.theme_combo.currentIndex() == 0 else "light",
            "temp_warn": self.temp_warn_spin.value(),
            "temp_danger": self.temp_danger_spin.value(),
            "ring_mode": "temp" if self._temp_mode() else "percent",
        }
        self.settings_changed.emit(cfg)

    def load_settings(self, cfg):
        self.interval_spin.setValue(int(cfg.get("interval", 1)))
        self.warn_spin.setValue(int(cfg.get("warn", 80)))
        self.danger_spin.setValue(int(cfg.get("danger", 90)))
        self.temp_warn_spin.setValue(int(cfg.get("temp_warn", 60)))
        self.temp_danger_spin.setValue(int(cfg.get("temp_danger", 80)))
        self.mode_combo.setCurrentIndex(1 if cfg.get("ring_mode") == "temp" else 0)
        self.theme_combo.setCurrentIndex(0 if cfg.get("theme", "dark") == "dark" else 1)
        self.overlay_check.setChecked(bool(cfg.get("show_overlay", True)))

    def closeEvent(self, e):
        e.ignore()  # 关闭 = 隐藏到托盘
        self.hide()

    def update_snapshot(self, snap):
        temp_mode = self._temp_mode()
        temps = {"cpu": snap.temp, "gpu": snap.gpu_temp,
                 "mem": snap.mem_temp, "disk": snap.disk_temp}
        for key, val in (("cpu", snap.cpu), ("gpu", snap.gpu),
                         ("mem", snap.mem), ("disk", snap.disk)):
            ring = self.boxes[key].ring
            if temp_mode:  # 温度模式:各环显示对应温度,无传感器显示 --
                ring.set_unit("°")
                ring.set_value(None if temps[key] is None else float(temps[key]))
            else:
                ring.set_unit("")
                ring.set_value(None if val is None else float(val))
        if snap.gpu_mem is not None:
            self.boxes["gpu"].ring.set_sub(f"显存 {snap.gpu_mem:.0f}%")

        hints = []
        if not HAS_GPU:
            hints.append("未检测到 GPU")
        if temp_mode and snap.temp is None:
            hints.append("未检测到温度传感器,以管理员身份运行可启用")
        elif temp_mode and snap.mem_temp is None:
            hints.append("内存无温度传感器")
        self.subtitle.setText(";".join(hints))

        self.detail.setText(
            f"网络  ↑ {fmt_speed(snap.net_up)}   ↓ {fmt_speed(snap.net_down)}"
            f"      磁盘  读 {snap.disk_read:.2f} MB/s   写 {snap.disk_write:.2f} MB/s")

    def update_history(self, collector):
        temp_mode = self._temp_mode()
        for key in ("cpu", "gpu", "mem", "disk"):
            if temp_mode:
                data = collector.history(f"temp_{key}")
                warn = self.temp_warn_spin.value()
                danger = self.temp_danger_spin.value()
            else:
                data = collector.history(key)
                warn = self.warn_spin.value()
                danger = self.danger_spin.value()
            spark = self.boxes[key].spark
            spark.set_data(data)
            if data and data[-1] is not None:
                spark.color = level_color(data[-1], warn, danger)
