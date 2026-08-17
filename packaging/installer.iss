; Inno Setup script for the Windows installer.
;
; Built by the release workflow with:
;   ISCC.exe packaging\installer.iss
;
; Expects PyInstaller to have produced dist\MT Sync\ first.

#define AppName "MT Sync"
#define AppVersion "0.1.0"
#define AppPublisher "matu-tr"
#define AppExe "MT Sync.exe"

[Setup]
AppId={{8D5A3C21-7E4B-4F19-9A62-1C0E5B7D3A84}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppSupportURL=https://github.com/matu-tr/mt-sync-py
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=MT-Sync-{#AppVersion}-windows-x64-setup
SetupIconFile=icon.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; Installing per-user needs no administrator prompt, which matters for an
; unsigned build people are already being warned about.
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked
Name: "startup"; Description: "Start {#AppName} when I sign in"; GroupDescription: "Startup:"; Flags: unchecked

[Files]
Source: "..\dist\MT Sync\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon
Name: "{userstartup}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: startup

[Run]
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; The sync database and settings are deliberately left behind: uninstalling
; should not throw away the record of what has already been synced.
Type: filesandordirs; Name: "{app}"
