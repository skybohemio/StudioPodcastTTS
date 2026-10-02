; Installeur Studio Podcast TTS (Voxtral) — Windows 11 x64
#define AppName "Studio Podcast TTS"
#define AppVersion "1.5.1"
#define AppPublisher "StudioPodcastTTS"
#define AppExe "StudioPodcastTTS.exe"

[Setup]
AppId={{7E3C9A2F-5D1B-4E8A-9C6D-2F1A8B3E5D70}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\StudioPodcastTTS
DefaultGroupName={#AppName}
OutputDir=..\dist\installer
OutputBaseFilename=StudioPodcastTTS-Setup-{#AppVersion}
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2/ultra64
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.22000
WizardStyle=modern
PrivilegesRequired=lowest

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Créer un raccourci sur le bureau"; GroupDescription: "Raccourcis :"

[Files]
Source: "..\dist\StudioPodcastTTS\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Désinstaller {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Lancer {#AppName}"; Flags: nowait postinstall skipifsilent
