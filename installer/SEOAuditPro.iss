; Inno Setup script for the SEO Audit Pro desktop client.
; Build: "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\SEOAuditPro.iss
; Input:  dist\SEOAuditPro\ (PyInstaller one-folder build)
; Output: dist\SEOAuditPro-Setup-3.0.0.exe

#define AppName "SEO Audit Pro"
#define AppVersion "3.0.0"
#define AppPublisher "Mindcob"
#define AppExe "SEOAuditPro.exe"

[Setup]
AppId={{6F0B7E7A-3C55-4C84-9B2B-4D7E0F1C2A11}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=SEOAuditPro-Setup-{#AppVersion}
SetupIconFile=..\seo_audit_pro\assets\app.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequiredOverridesAllowed=dialog

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "..\dist\SEOAuditPro\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent
