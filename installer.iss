; Inno Setup 6 script for the Engineering Console installer. Built by build_windows.ps1 (it passes /DAppVersion=x.y.z).
; Installs per user by default (no administrator password, no UAC prompt); the installer offers "all users" as a choice.
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppName "Engineering Console"
#define AppExe "Engineering Console.exe"
#define Root SourcePath + "..\.."

[Setup]
AppId={{8F3B6A52-4C1E-4D7A-9B0E-2E5A7C14D9F3}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Ashira Resevic
AppCopyright=Copyright (c) 2026 Ashira Resevic. MIT License.
VersionInfoVersion={#AppVersion}.0
SourceDir={#Root}
OutputDir={#Root}\dist
OutputBaseFilename=engineering-console-{#AppVersion}-windows-x64-setup
SetupIconFile={#Root}\build\windows\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
LicenseFile={#Root}\LICENSE
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
Source: "dist\{#AppName}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Start {#AppName}"; Flags: nowait postinstall skipifsilent

; Settings and logs (%APPDATA%\Engineering Console, %LOCALAPPDATA%\Engineering Console) are deliberately NOT removed
; on uninstall, so reinstalling keeps your layout.
