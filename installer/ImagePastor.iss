#ifndef AppVersion
  #define AppVersion "1.2.0"
#endif
#ifndef PayloadDir
  #error PayloadDir must point to the clean release payload
#endif
#ifndef ReleaseDir
  #define ReleaseDir "..\releases"
#endif

[Setup]
AppId={{AD5BEE14-FE42-4984-88F6-10F697AF2477}
AppName=ImagePastor
AppVersion={#AppVersion}
AppPublisher=khisanthax
AppPublisherURL=https://github.com/khisanthax/imagepastor
DefaultDirName={localappdata}\Programs\ImagePastor
DefaultGroupName=ImagePastor
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
MinVersion=10.0
OutputDir={#ReleaseDir}
OutputBaseFilename=ImagePastor-Setup-{#AppVersion}-windows-x64
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\ImagePastor.exe
AppMutex=ImagePastorSingleInstance,ImagePastorSupervisor,ImagePastorIndependentObserver
CloseApplications=no
RestartApplications=no
DisableProgramGroupPage=yes
SetupLogging=yes

[Tasks]
Name: "startup"; Description: "Start ImagePastor when I sign in to Windows"
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked
Name: "diagnostics"; Description: "Run the independent lifecycle diagnostics at sign-in"; Flags: unchecked

[Files]
Source: "{#PayloadDir}\ImagePastor.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#PayloadDir}\ImagePastorDiagnostics.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#PayloadDir}\config.example.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#PayloadDir}\config.json"; DestDir: "{app}"; Flags: onlyifdoesntexist uninsneveruninstall
Source: "{#PayloadDir}\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#PayloadDir}\ImagePastor-Chime\*"; DestDir: "{app}\ImagePastor-Chime"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#PayloadDir}\ImagePastor-Codex\*"; DestDir: "{app}\ImagePastor-Codex"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#PayloadDir}\ThirdPartyNotices\*"; DestDir: "{app}\ThirdPartyNotices"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\ImagePastor"; Filename: "{app}\ImagePastor.exe"; WorkingDir: "{app}"
Name: "{group}\ImagePastor Instructions"; Filename: "{app}\README.md"
Name: "{autodesktop}\ImagePastor"; Filename: "{app}\ImagePastor.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "ImagePastor"; ValueData: """{app}\ImagePastor.exe"""; Flags: uninsdeletevalue; Tasks: startup
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "ImagePastorDiagnostics"; ValueData: """{app}\ImagePastorDiagnostics.exe"""; Flags: uninsdeletevalue; Tasks: diagnostics

[Run]
Filename: "{app}\ImagePastor.exe"; Description: "Start ImagePastor"; Flags: nowait postinstall skipifsilent
Filename: "{app}\ImagePastorDiagnostics.exe"; Flags: nowait skipifsilent; Tasks: diagnostics
