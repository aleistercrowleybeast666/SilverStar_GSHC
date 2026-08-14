#define MyAppName "SilverStar_GSHC"
#define MyAppInternalName "SilverStar_GSHC"
#define MyAppVersion "0.0.3"
#define MyAppExeName "SilverStar_GSHC.exe"

[Setup]
AppId={{6DD6E593-90CC-47B1-9034-0A17BE7092D5}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=SilverStar
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=output
OutputBaseFilename={#MyAppInternalName}_Setup_v{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName={#MyAppName}

[Files]
Source: "..\dist\{#MyAppInternalName}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Code]
var
  DataDirectoryPage: TInputDirWizardPage;

procedure InitializeWizard;
begin
  DataDirectoryPage := CreateInputDirPage(
    wpSelectDir,
    'Ground Station data directory',
    'Choose where logs, exports, and cached data are stored.',
    'Uninstalling the application will not delete this directory.',
    False,
    ''
  );
  DataDirectoryPage.Add('');
  DataDirectoryPage.Values[0] := 'D:\SilverStar_GSHC_Data';
end;

function JsonPath(Value: String): String;
begin
  StringChangeEx(Value, '\', '\\', True);
  Result := Value;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  DataRoot: String;
  ConfigDirectory: String;
  ConfigText: String;
begin
  if CurStep <> ssPostInstall then
    exit;

  DataRoot := DataDirectoryPage.Values[0];
  ForceDirectories(DataRoot);
  ForceDirectories(DataRoot + '\logs');
  ForceDirectories(DataRoot + '\data');
  ConfigDirectory := ExpandConstant('{app}\config');
  ForceDirectories(ConfigDirectory);
  ConfigText := '{' + #13#10 +
    '  "data_root": "' + JsonPath(DataRoot) + '",' + #13#10 +
    '  "logs_dir": "' + JsonPath(DataRoot + '\logs') + '",' + #13#10 +
    '  "data_dir": "' + JsonPath(DataRoot + '\data') + '",' + #13#10 +
    '  "migration_requested": false,' + #13#10 +
    '  "migration_conflict_policy": "overwrite",' + #13#10 +
    '  "previous_data_root": ""' + #13#10 +
    '}' + #13#10;
  SaveStringToFile(ConfigDirectory + '\user_paths.json', ConfigText, False);
end;
