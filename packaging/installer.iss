; Optional per-user installer; compile separately with Inno Setup when available.
#define AppVersion GetFileVersion("..\dist\PhotoExplorer\PhotoExplorer.exe")

[Setup]
AppId={{2899EA31-9D55-43C1-B41C-CE044BB69C7F}
AppName=Photo Explorer
AppVersion={#AppVersion}
AppPublisher=Photo Explorer
DefaultDirName={localappdata}\Programs\PhotoExplorer
DefaultGroupName=Photo Explorer
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=PhotoExplorer-{#AppVersion}-setup
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\PhotoExplorer.exe
Compression=lzma2
SolidCompression=yes
CloseApplications=yes

[Files]
Source: "..\dist\PhotoExplorer\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Photo Explorer"; Filename: "{app}\PhotoExplorer.exe"; WorkingDir: "{app}"

[Run]
Filename: "{app}\PhotoExplorer.exe"; Description: "Open Photo Explorer"; Flags: nowait postinstall skipifsilent
