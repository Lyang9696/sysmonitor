"""亮/暗主题 QSS 与 Windows 11 Acrylic 毛玻璃背景。"""
import ctypes
import os
import tempfile

_ICON_DIR = os.path.join(tempfile.gettempdir(), "sysmonitor")


def _arrow(name, up, color):
    """生成上下小箭头 png(QSS image 引用),返回 QSS 可用的路径。"""
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QColor, QPainter, QPixmap, QPolygonF
    os.makedirs(_ICON_DIR, exist_ok=True)
    path = os.path.join(_ICON_DIR, name)
    pm = QPixmap(10, 6)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    poly = (QPolygonF([QPointF(1, 4.6), QPointF(9, 4.6), QPointF(5, 0.9)]) if up
            else QPolygonF([QPointF(1, 1.4), QPointF(9, 1.4), QPointF(5, 5.1)]))
    p.drawPolygon(poly)
    p.end()
    pm.save(path)
    return path.replace("\\", "/")


def qss(dark, glass):
    """主窗口样式表;glass=True 时背景半透明,透出系统 Acrylic 模糊。"""
    if dark:
        win = "rgba(28,28,30,185)" if glass else "#14161f"
        text, sub = "#f2f2f7", "rgba(235,235,245,155)"
        box, border = "rgba(120,120,128,80)", "rgba(120,120,128,120)"
        field, fborder = "rgba(99,99,105,150)", "rgba(120,120,128,140)"
        hborder, accent, warn = "rgba(180,180,190,220)", "#0a84ff", "#f5c542"
        arrow_c = "#c7c7cc"
    else:
        win = "rgba(246,246,248,190)" if glass else "#f2f2f7"
        text, sub = "#1c1c1e", "rgba(60,60,67,150)"
        box, border = "rgba(120,120,128,50)", "rgba(120,120,128,85)"
        field, fborder = "rgba(255,255,255,235)", "rgba(120,120,128,105)"
        hborder, accent, warn = "rgba(99,99,105,190)", "#007aff", "#b45309"
        arrow_c = "#636366"
    tag = "d" if dark else "l"
    up = _arrow(f"up_{tag}.png", True, arrow_c)
    down = _arrow(f"down_{tag}.png", False, arrow_c)
    return f"""
QMainWindow, QWidget#central {{ background: {win}; }}
QLabel {{ color: {text}; }}
QLabel#subtitle {{ color: {warn}; font-size: 12px; }}
QLabel#detail {{ color: {sub}; font-size: 12px; }}
QLabel#fieldLabel {{ color: {sub}; font-size: 12px; }}
QGroupBox {{
    color: {sub}; border: 1px solid {border}; border-radius: 12px;
    margin-top: 12px; padding: 10px 10px 8px 10px; font-size: 12px;
    background: {box};
}}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; }}
QSpinBox, QComboBox {{
    background: {field}; color: {text}; border: 1px solid {fborder};
    border-radius: 8px; padding: 3px 24px 3px 10px;
    min-width: 54px; min-height: 20px;
}}
QSpinBox:hover, QComboBox:hover {{ border: 1px solid {hborder}; }}
QSpinBox:focus {{ border: 1px solid {accent}; }}
QSpinBox::up-button {{
    subcontrol-origin: border; subcontrol-position: top right;
    width: 17px; border: none; background: transparent;
}}
QSpinBox::down-button {{
    subcontrol-origin: border; subcontrol-position: bottom right;
    width: 17px; border: none; background: transparent;
}}
QSpinBox::up-button:hover, QSpinBox::down-button:hover {{
    background: {box}; border-radius: 5px;
}}
QSpinBox::up-arrow {{ image: url({up}); width: 8px; height: 5px; }}
QSpinBox::down-arrow {{ image: url({down}); width: 8px; height: 5px; }}
QComboBox::drop-down {{ border: none; width: 20px; }}
QComboBox::down-arrow {{ image: url({down}); width: 8px; height: 5px; }}
QComboBox QAbstractItemView {{
    background: {field}; color: {text}; border: 1px solid {fborder};
    selection-background-color: rgba(120,120,128,110);
    outline: none;
}}
QCheckBox {{ color: {text}; spacing: 6px; }}
"""


class _Margins(ctypes.Structure):
    _fields_ = [("left", ctypes.c_int), ("right", ctypes.c_int),
                ("top", ctypes.c_int), ("bottom", ctypes.c_int)]


def apply_glass(widget, dark):
    """给窗口启用 Win11 Acrylic 毛玻璃;成功(可半透明)返回 True。"""
    try:
        hwnd = int(widget.winId())
        dwm = ctypes.windll.dwmapi
        v = ctypes.c_int(1 if dark else 0)
        dwm.DwmSetWindowAttribute(ctypes.c_void_p(hwnd), 20, ctypes.byref(v), 4)  # 深/浅标题栏
        dwm.DwmExtendFrameIntoClientArea(
            ctypes.c_void_p(hwnd), ctypes.byref(_Margins(-1, -1, -1, -1)))
        v = ctypes.c_int(3)  # DWMSBT_TRANSIENTWINDOW = Acrylic
        return dwm.DwmSetWindowAttribute(ctypes.c_void_p(hwnd), 38, ctypes.byref(v), 4) == 0
    except Exception:
        return False
