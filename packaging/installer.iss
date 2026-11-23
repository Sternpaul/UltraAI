; Inno Setup script for UltraAI Studio (built by .github/workflows/windows-installer.yml)
; Per-user install, no admin rights needed.

#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif

[Setup]
AppId={{6F1E5A9C-2B7D-4C8E-9A51-7D3E0C1B4F21}
AppName=UltraAI Studio
AppVersion={#AppVersion}
AppPublisher=UltraAI
DefaultDirName={localappdata}\Programs\UltraAI Studio
DefaultGroupName=UltraAI Studio
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=UltraAI-Studio-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\UltraAI Studio.exe
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "..\dist\UltraAI Studio\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\UltraAI Studio"; Filename: "{app}\UltraAI Studio.exe"
Name: "{group}\Uninstall UltraAI Studio"; Filename: "{uninstallexe}"
Name: "{userdesktop}\UltraAI Studio"; Filename: "{app}\UltraAI Studio.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\UltraAI Studio.exe"; Description: "Start UltraAI Studio"; Flags: nowait postinstall skipifsilent
