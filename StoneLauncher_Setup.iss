; ============================================================
; Stone Launcher — Inno Setup Script
;
; Supports:
;   - Clean installation
;   - Updating an existing installation
;   - Existing install directory detection
;   - Existing version detection
;   - Closing the running launcher before replacement
;
; Place this file beside:
;   dist\StoneLauncher.exe
;   img\logo.ico
;   img\logo.png
;   jre\   (bundled Eclipse Temurin JRE 21, created by bundle_jre.py)
;   jre8\  (bundled Eclipse Temurin JRE 8)
;   jre17\ (bundled Eclipse Temurin JRE 17)
;   jre25\ (bundled Eclipse Temurin JRE 25)
; ============================================================

#define AppName        "Stone Launcher"
#define AppExeName     "StoneLauncher.exe"
#define AppVersion     "3.0.0"
#define Publisher      "Oblix Interactive"
#define AppURL         "https://github.com/AlexumYT/StoneLauncher"
#define AppIdValue     "A3F8C2D1-4B7E-4F2A-9C6D-1E8B3A5F7D9C"

[Setup]
; Keep this AppId unchanged between releases.
; Inno Setup uses it to recognize an existing installation.
AppId={{{#AppIdValue}}

AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#Publisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}

DefaultDirName={autopf}\Stone Launcher
DefaultGroupName={#AppName}

; Reuse the previous installation directory and Start Menu group.
UsePreviousAppDir=yes
UsePreviousGroup=yes
UsePreviousTasks=yes

; Only show folder/group pages when needed.
DisableDirPage=auto
DisableProgramGroupPage=auto
DisableReadyPage=no

OutputDir=installer
OutputBaseFilename=StoneLauncher_Setup_{#AppVersion}

SetupIconFile=img\logo.ico
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName}

Compression=lzma2/ultra64
SolidCompression=yes

WizardStyle=modern
WizardResizable=no

VersionInfoVersion={#AppVersion}
VersionInfoCompany={#Publisher}
VersionInfoDescription={#AppName} Installer
VersionInfoProductName={#AppName}
VersionInfoProductVersion={#AppVersion}

PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog

; Ask Windows Restart Manager to close the launcher before replacing it.
CloseApplications=yes
CloseApplicationsFilter={#AppExeName}
RestartApplications=no

; Allow the installer to replace the existing executable.
AllowCancelDuringInstall=no

; Create and maintain the uninstall registry information.
Uninstallable=yes
CreateUninstallRegKey=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; \
    Description: "Create a &desktop shortcut"; \
    GroupDescription: "Shortcuts:"

Name: "startmenuicon"; \
    Description: "Create a &Start Menu shortcut"; \
    GroupDescription: "Shortcuts:"

[InstallDelete]
; Remove old bundled image files before copying the new set.
; User data under AppData\StoneLauncher is not affected.
Type: filesandordirs; Name: "{app}\img"

[Files]
Source: "dist\{#AppExeName}"; \
    DestDir: "{app}"; \
    Flags: ignoreversion restartreplace

Source: "img\*"; \
    DestDir: "{app}\img"; \
    Flags: ignoreversion recursesubdirs createallsubdirs

; Bundled Java 21 runtime. StoneLauncher prefers <install>\jre\bin\java.exe
; and only falls back to provisioning Mojang's runtime when it is absent.
Source: "jre\*"; \
    DestDir: "{app}\jre"; \
    Flags: ignoreversion recursesubdirs createallsubdirs restartreplace

; Bundled Java 8 runtime. 1.8.9 and 1.16.5 require exactly Java 8.
Source: "jre8\*"; \
    DestDir: "{app}\jre8"; \
    Flags: ignoreversion recursesubdirs createallsubdirs restartreplace

; Bundled Java 25 runtime for Minecraft 26.1.2 and newer.
; Bundled Java 17 runtime for 1.17.1 through 1.20.1.
Source: "jre17\*"; \
    DestDir: "{app}\jre17"; \
    Flags: ignoreversion recursesubdirs createallsubdirs restartreplace

Source: "jre25\*"; \
    DestDir: "{app}\jre25"; \
    Flags: ignoreversion recursesubdirs createallsubdirs restartreplace

[Icons]
Name: "{autodesktop}\{#AppName}"; \
    Filename: "{app}\{#AppExeName}"; \
    WorkingDir: "{app}"; \
    IconFilename: "{app}\img\logo.ico"; \
    Tasks: desktopicon

Name: "{group}\{#AppName}"; \
    Filename: "{app}\{#AppExeName}"; \
    WorkingDir: "{app}"; \
    IconFilename: "{app}\img\logo.ico"; \
    Tasks: startmenuicon

Name: "{group}\Uninstall {#AppName}"; \
    Filename: "{uninstallexe}"; \
    Tasks: startmenuicon

[Run]
Filename: "{app}\{#AppExeName}"; \
    Description: "Launch {#AppName}"; \
    WorkingDir: "{app}"; \
    Flags: nowait postinstall skipifsilent runascurrentuser

[UninstallDelete]
Type: files; Name: "{app}\{#AppExeName}"
Type: filesandordirs; Name: "{app}\img"
Type: filesandordirs; Name: "{app}\jre"
Type: filesandordirs; Name: "{app}\jre8"
Type: filesandordirs; Name: "{app}\jre17"
Type: filesandordirs; Name: "{app}\jre25"
Type: files; Name: "{app}\stone_update.bat"
Type: files; Name: "{app}\StoneLauncher_new.exe"

[Code]
var
  InstalledVersion: String;
  IsUpdateInstall: Boolean;


function GetUninstallRegistryPath(): String;
begin
  Result :=
    'Software\Microsoft\Windows\CurrentVersion\Uninstall\' +
    '{#AppIdValue}_is1';
end;


function ReadInstalledVersion(): String;
var
  RegistryPath: String;
begin
  Result := '';
  RegistryPath := GetUninstallRegistryPath();

  { Check the 64-bit machine registry }
  if RegQueryStringValue(
       HKLM64,
       RegistryPath,
       'DisplayVersion',
       Result
     ) then
  begin
    Exit;
  end;

  { Check the 32-bit machine registry }
  if RegQueryStringValue(
       HKLM32,
       RegistryPath,
       'DisplayVersion',
       Result
     ) then
  begin
    Exit;
  end;

  { Check for a per-user installation }
  RegQueryStringValue(
    HKCU,
    RegistryPath,
    'DisplayVersion',
    Result
  );
end;


function InitializeSetup(): Boolean;
var
  MessageText: String;
begin
  Result := True;

  InstalledVersion := ReadInstalledVersion();
  IsUpdateInstall := InstalledVersion <> '';

  if IsUpdateInstall then
  begin
    MessageText :=
      '{#AppName} is already installed.' + #13#10 + #13#10 +
      'Installed version: ' + InstalledVersion + #13#10 +
      'Setup version: {#AppVersion}' + #13#10 + #13#10 +
      'The existing installation will be updated.';

    MsgBox(
      MessageText,
      mbInformation,
      MB_OK
    );
  end;
end;


procedure InitializeWizard();
begin
  if IsUpdateInstall then
  begin
    WizardForm.Caption :=
      'Update {#AppName} to version {#AppVersion}';

    WizardForm.WelcomeLabel1.Caption :=
      'Update {#AppName}';

    WizardForm.WelcomeLabel2.Caption :=
      'This setup will update the existing installation from version ' +
      InstalledVersion + ' to version {#AppVersion}.' + #13#10 + #13#10 +
      'Your launcher account, Minecraft files, preferences, and other ' +
      'data stored in AppData will not be removed.';
  end;
end;


procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    if IsUpdateInstall then
    begin
      Log(
        'Stone Launcher was updated from version ' +
        InstalledVersion +
        ' to version {#AppVersion}.'
      );
    end
    else
    begin
      Log(
        'Stone Launcher version {#AppVersion} was installed.'
      );
    end;
  end;
end;