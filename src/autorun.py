"""开机自启:HKCU Run 注册表项(无需管理员,HKCU 对当前用户生效)。"""
import sys
from pathlib import Path

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_APP_NAME = "SysMonitor"


def _command():
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    return f'"{sys.executable}" "{Path(__file__).with_name("main.py")}"'


def is_autorun():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
            return winreg.QueryValueEx(k, _APP_NAME)[0] == _command()
    except OSError:
        return False


def set_autorun(on):
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if on:
                winreg.SetValueEx(k, _APP_NAME, 0, winreg.REG_SZ, _command())
            else:
                winreg.DeleteValue(k, _APP_NAME)
        return True
    except OSError:
        return False
