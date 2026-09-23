[Setup]
AppId={{6F2A9C4E-1B3D-4E8F-9C5A-SYSMONITOR10}
AppName=系统监控
AppVersion=1.0.3
AppPublisher=SysMonitor
DefaultDirName={autopf}\SysMonitor
DefaultGroupName=系统监控
OutputDir=..\installer
OutputBaseFilename=SysMonitorSetup-1.0.3
Compression=lzma2/max
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\sysmonitor.exe

[Files]
; Excludes: 裁剪无用组件(软件OpenGL/QML/PDF/翻译)与运行时文件,减小体积
Source: "..\dist\sysmonitor\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion; Excludes: "opengl32sw.dll,Qt6Pdf.dll,Qt6Quick*.dll,Qt6Qml*.dll,Qt6QuickControls2.dll,translations,qml,config.json,crash.log"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务:"

[Icons]
Name: "{group}\系统监控"; Filename: "{app}\sysmonitor.exe"
Name: "{group}\卸载 系统监控"; Filename: "{uninstallexe}"
Name: "{autodesktop}\系统监控"; Filename: "{app}\sysmonitor.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\sysmonitor.exe"; Description: "立即运行 系统监控"; Flags: nowait postinstall skipifsilent runascurrentuser
