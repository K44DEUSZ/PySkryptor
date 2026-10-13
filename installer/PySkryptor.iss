#ifndef ReleaseRoot
  #error Supply /DReleaseRoot with the assembled application directory
#endif
#ifndef AppVersion
  #define AppVersion "2.0-alpha"
#endif

[Setup]
AppId={{D43C074C-49C1-4D6F-856B-3C292D4F888A}
AppName=PySkryptor
AppVersion={#AppVersion}
AppPublisher=Bartosz Golat
AppPublisherURL=https://github.com/K44DEUSZ/PySkryptor
DefaultDirName={localappdata}\Programs\PySkryptor
DefaultGroupName=PySkryptor
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
AppMutex=Local\PySkryptorDesktop
CloseApplications=no
RestartApplications=no
LicenseFile={#ReleaseRoot}\LICENSE
SetupIconFile={#ReleaseRoot}\assets\icons\favicon.ico
UninstallDisplayIcon={app}\PySkryptor.exe
OutputDir=..\dist\installer
OutputBaseFilename=PySkryptor-{#AppVersion}-setup
Compression=lzma2/fast
SolidCompression=no
WizardStyle=modern
DisableProgramGroupPage=yes
UsePreviousAppDir=yes
UsePreviousTasks=yes

[Languages]
Name: "pl"; MessagesFile: "compiler:Languages\Polish.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[Types]
Name: "standard"; Description: "{cm:StandardInstallation}"
Name: "custom"; Description: "{cm:CustomInstallation}"; Flags: iscustom

[Components]
Name: "base"; Description: "{cm:BaseComponent}"; Types: standard custom; Flags: fixed
#ifdef IncludeCuda
Name: "cuda"; Description: "{cm:CudaComponent}"; Types: custom
#endif

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; Flags: unchecked

[Files]
Source: "{#ReleaseRoot}\*"; DestDir: "{app}"; Excludes: "runtimes\cuda\*,licenses\cuda\*"; Flags: ignoreversion recursesubdirs createallsubdirs; Components: base
#ifdef IncludeCuda
Source: "{#ReleaseRoot}\runtimes\cuda\*"; DestDir: "{app}\runtimes\cuda"; Flags: ignoreversion recursesubdirs createallsubdirs; Components: cuda
Source: "{#ReleaseRoot}\licenses\cuda\*"; DestDir: "{app}\licenses\cuda"; Flags: ignoreversion recursesubdirs createallsubdirs; Components: cuda
#endif

[Icons]
Name: "{group}\PySkryptor"; Filename: "{app}\PySkryptor.exe"
Name: "{autodesktop}\PySkryptor"; Filename: "{app}\PySkryptor.exe"; Tasks: desktopicon

[InstallDelete]
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\runtimes\cpu"
Type: filesandordirs; Name: "{app}\licenses\gui"
Type: filesandordirs; Name: "{app}\licenses\cpu"
Type: filesandordirs; Name: "{app}\runtimes\cuda"
Type: filesandordirs; Name: "{app}\licenses\cuda"

[Run]
Filename: "{app}\PySkryptor.exe"; Description: "{cm:LaunchApp}"; Flags: nowait postinstall skipifsilent

[CustomMessages]
pl.StandardInstallation=Instalacja standardowa
en.StandardInstallation=Standard installation
pl.CustomInstallation=Wybierz składniki
en.CustomInstallation=Choose components
pl.BaseComponent=PySkryptor i silnik dla procesora
en.BaseComponent=PySkryptor and CPU engine
pl.CudaComponent=Silnik NVIDIA (Turing i nowsze karty, CUDA 13.2)
en.CudaComponent=NVIDIA engine (Turing and newer GPUs, CUDA 13.2)
pl.LaunchApp=Uruchom PySkryptor
en.LaunchApp=Launch PySkryptor
