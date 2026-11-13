; Inno Setup script for MeshLens.  Build:  iscc /DAppVersion=0.2.0 packaging\installer.iss
; Expects the PyInstaller output in dist\MeshLens.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6F3C1E2A-8B7D-4E51-9C1F-4D2B7A9E0C11}
AppName=MeshLens
AppVersion={#AppVersion}
AppPublisher=MeshLens
AppPublisherURL=https://github.com/markbac/homenetworkdiag
DefaultDirName={autopf}\MeshLens
DefaultGroupName=MeshLens
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=MeshLens-{#AppVersion}-setup
SetupIconFile=meshlens.ico
UninstallDisplayIcon={app}\MeshLens.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
CloseApplications=force
RestartApplications=no

[Tasks]
Name: "startup"; Description: "Start MeshLens when I sign in to Windows"; GroupDescription: "Options:"
Name: "firewall"; Description: "Let phones and laptops on my home (private) network open the dashboard"; GroupDescription: "Options:"
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Options:"; Flags: unchecked

[Files]
Source: "..\dist\MeshLens\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\MeshLens"; Filename: "{app}\MeshLens.exe"; Parameters: "--tray"
Name: "{group}\MeshLens dashboard"; Filename: "http://localhost:8787/"
Name: "{group}\Uninstall MeshLens"; Filename: "{uninstallexe}"
Name: "{autodesktop}\MeshLens"; Filename: "{app}\MeshLens.exe"; Parameters: "--tray"; Tasks: desktopicon

[Registry]
; Per-user sign-in entry; the tray menu's "Start with Windows" toggles the same value.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "MeshLens"; \
  ValueData: """{app}\MeshLens.exe"" --tray"; Flags: uninsdeletevalue; Tasks: startup

[Run]
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""MeshLens dashboard"" dir=in action=allow program=""{app}\MeshLens.exe"" protocol=TCP localport=8787 profile=private"; \
  Flags: runhidden; Tasks: firewall; StatusMsg: "Allowing the dashboard on private networks..."
Filename: "{app}\MeshLens.exe"; Parameters: "--tray"; Description: "Start MeshLens now"; Flags: nowait postinstall runasoriginaluser
; Not skipped in silent mode: an in-app update runs the installer with /SILENT and MeshLens starts again afterwards.

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/IM MeshLens.exe /F"; Flags: runhidden; RunOnceId: "StopMeshLens"
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""MeshLens dashboard"""; Flags: runhidden; RunOnceId: "RemoveFirewallRule"

[Messages]
FinishedLabel=MeshLens is installed. It runs in the system tray; open http://localhost:8787 to create your dashboard password, then enter the router password under Settings.

; Your data (settings, history, logs) stays in %LOCALAPPDATA%\MeshLens and is kept on uninstall.
; Passwords stay in the Windows Credential Manager under "MeshLens".
