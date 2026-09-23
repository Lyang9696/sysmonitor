@echo off
rem repo root = parent dir of this bat (works from any cwd / double-click)
cd /d "%~dp0.."
echo [1/3] pyinstaller onedir...
python -m PyInstaller --noconfirm --windowed --uac-admin --name sysmonitor --icon assets\icon.ico --add-data "assets\app_icon.png;." --paths src --hidden-import win32timezone --hidden-import wmi --hidden-import win32com.client --hidden-import PySide6.QtNetwork src\main.py > build_log.txt 2>&1
echo [2/3] pyinstaller onefile...
python -m PyInstaller --noconfirm --windowed --uac-admin --name sysmonitor-portable --icon assets\icon.ico --add-data "assets\app_icon.png;." --paths src --hidden-import win32timezone --hidden-import wmi --hidden-import win32com.client --hidden-import PySide6.QtNetwork --onefile src\main.py >> build_log.txt 2>&1
echo [3/3] inno build...
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" builder\setup.iss >> build_log.txt 2>&1
echo ALL_DONE >> build_log.txt
