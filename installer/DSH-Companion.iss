#define AppVersion "0.2.0"
#define AppName "DSH 伴航"

[Setup]
AppId={{D6C433B6-0BE1-4509-82D1-2B02C6A0DE29}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} v{#AppVersion}
AppPublisher=toydream525
AppPublisherURL=https://github.com/toydream525/deepseek-harness-companion
AppSupportURL=https://github.com/toydream525/deepseek-harness-companion/issues
AppUpdatesURL=https://github.com/toydream525/deepseek-harness-companion/releases
DefaultDirName={localappdata}\Programs\DSH-Companion
UsePreviousAppDir=yes
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\release
OutputBaseFilename=DSH-Companion-Setup-v{#AppVersion}-windows-x64
SetupIconFile=..\manager\assets\companion.ico
UninstallDisplayIcon={app}\DSH-Companion.exe
UninstallDisplayName={#AppName}
VersionInfoVersion=0.2.0.0
VersionInfoDescription=DSH Companion installer
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=no
RestartApplications=no
LicenseFile=..\LICENSE

[Files]
Source: "..\release\DSH-Companion\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加选项："; Flags: unchecked

[Icons]
Name: "{group}\DSH 伴航"; Filename: "{app}\DSH-Companion.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\DSH 伴航"; Filename: "{app}\DSH-Companion.exe"; WorkingDir: "{app}"; Tasks: desktopicon
