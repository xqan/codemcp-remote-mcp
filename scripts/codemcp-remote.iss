#ifndef SourceDir
  #error SourceDir must be provided by build-windows-installer.ps1
#endif
#ifndef OutputDir
  #error OutputDir must be provided by build-windows-installer.ps1
#endif
#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif
#ifndef InstallerAppId
  #define InstallerAppId "{{A26B4BA3-1D96-4F1A-95C4-9984C941A1E1}"
#endif
#ifndef InstallerAppName
  #define InstallerAppName "codemcp-remote"
#endif
#ifndef InstallerGroupName
  #define InstallerGroupName "codemcp-remote"
#endif
#ifndef ProductRegistryKey
  #define ProductRegistryKey "Software\codemcp-remote"
#endif

[Setup]
AppId={#InstallerAppId}
AppName={#InstallerAppName}
AppVersion={#AppVersion}
AppPublisher=codemcp-remote contributors
DefaultDirName={localappdata}\Programs\codemcp-remote
DefaultGroupName={#InstallerGroupName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=codemcp-remote-setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
SetupLogging=yes
ChangesEnvironment=yes
CloseApplications=yes
RestartApplications=no
UsePreviousAppDir=yes
DirExistsWarning=no
LicenseFile={#SourceDir}\LICENSE
UninstallDisplayIcon={app}\codemcp-remote.exe
VersionInfoVersion=0.1.0.0

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Tasks]
Name: "addtopath"; Description: "Add codemcp-remote to the current user's PATH"; Flags: unchecked

[Icons]
Name: "{group}\Start codemcp-remote"; Filename: "{app}\codemcp-start.cmd"; WorkingDir: "{app}"; IconFilename: "{app}\codemcp-remote.exe"
Name: "{group}\Stop codemcp-remote"; Filename: "{app}\codemcp-stop.cmd"; WorkingDir: "{app}"; IconFilename: "{app}\codemcp-remote.exe"
Name: "{group}\codemcp-remote Doctor"; Filename: "{app}\codemcp-remote.exe"; Parameters: "doctor"; WorkingDir: "{app}"
Name: "{group}\codemcp-remote Folder"; Filename: "{app}"
Name: "{group}\Uninstall codemcp-remote"; Filename: "{uninstallexe}"

[UninstallRun]
Filename: "{app}\codemcp-remote.exe"; Parameters: "stop"; Flags: runhidden waituntilterminated skipifdoesntexist; Check: ShouldStopLifecycle

[Code]
const
  ProductRegistryKey = '{#ProductRegistryKey}';

function NormalizePath(Value: string): string;
begin
  Result := Lowercase(Trim(Value));
  while (Length(Result) > 3) and (Result[Length(Result)] = '\') do
    Delete(Result, Length(Result), 1);
end;

procedure SplitPath(Value: string; Entries: TStringList);
var
  Separator: Integer;
  Item: string;
begin
  Entries.Clear;
  while Value <> '' do
  begin
    Separator := Pos(';', Value);
    if Separator = 0 then
    begin
      Item := Value;
      Value := '';
    end
    else
    begin
      Item := Copy(Value, 1, Separator - 1);
      Delete(Value, 1, Separator);
    end;
    Item := Trim(Item);
    if Item <> '' then
      Entries.Add(Item);
  end;
end;

function JoinPath(Entries: TStringList): string;
var
  Index: Integer;
begin
  Result := '';
  for Index := 0 to Entries.Count - 1 do
  begin
    if Result <> '' then
      Result := Result + ';';
    Result := Result + Entries[Index];
  end;
end;

function ContainsPath(Entries: TStringList; Value: string): Boolean;
var
  Index: Integer;
  Expected: string;
begin
  Expected := NormalizePath(Value);
  Result := False;
  for Index := 0 to Entries.Count - 1 do
  begin
    if NormalizePath(Entries[Index]) = Expected then
    begin
      Result := True;
      Exit;
    end;
  end;
end;

procedure AddUserPath;
var
  CurrentPath: string;
  AppPath: string;
  Entries: TStringList;
begin
  AppPath := ExpandConstant('{app}');
  if not RegQueryStringValue(HKCU, 'Environment', 'Path', CurrentPath) then
    CurrentPath := '';

  Entries := TStringList.Create;
  try
    SplitPath(CurrentPath, Entries);
    if not ContainsPath(Entries, AppPath) then
    begin
      Entries.Add(AppPath);
      if not RegWriteStringValue(HKCU, 'Environment', 'Path', JoinPath(Entries)) then
        RaiseException('Unable to update the current user PATH.');
      RegWriteDWordValue(HKCU, ProductRegistryKey, 'PathAdded', 1);
    end;
  finally
    Entries.Free;
  end;
end;

procedure RemoveUserPath;
var
  CurrentPath: string;
  AppPath: string;
  Entries: TStringList;
  Index: Integer;
  PathAdded: Cardinal;
begin
  if not RegQueryDWordValue(HKCU, ProductRegistryKey, 'PathAdded', PathAdded) then
    Exit;
  if PathAdded <> 1 then
    Exit;

  AppPath := ExpandConstant('{app}');
  if not RegQueryStringValue(HKCU, 'Environment', 'Path', CurrentPath) then
    CurrentPath := '';

  Entries := TStringList.Create;
  try
    SplitPath(CurrentPath, Entries);
    for Index := Entries.Count - 1 downto 0 do
    begin
      if NormalizePath(Entries[Index]) = NormalizePath(AppPath) then
        Entries.Delete(Index);
    end;
    if not RegWriteStringValue(HKCU, 'Environment', 'Path', JoinPath(Entries)) then
      RaiseException('Unable to remove codemcp-remote from the current user PATH.');
    RegDeleteValue(HKCU, ProductRegistryKey, 'PathAdded');
  finally
    Entries.Free;
  end;
end;

function HasCommandLineSwitch(SwitchName: string): Boolean;
var
  Index: Integer;
begin
  Result := False;
  for Index := 1 to ParamCount do
  begin
    if CompareText(ParamStr(Index), SwitchName) = 0 then
    begin
      Result := True;
      Exit;
    end;
  end;
end;

function ShouldStopLifecycle: Boolean;
begin
  Result := not HasCommandLineSwitch('/NOSTOPLIFECYCLE');
end;

function PrepareToInstall(var NeedsRestart: Boolean): string;
var
  ExistingExe: string;
  ResultCode: Integer;
begin
  Result := '';
  if HasCommandLineSwitch('/NOSTOPLIFECYCLE') then
    Exit;
  ExistingExe := ExpandConstant('{app}\codemcp-remote.exe');
  if FileExists(ExistingExe) then
  begin
    if not Exec(ExistingExe, 'stop', '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
    begin
      Result := 'The existing codemcp-remote installation could not be stopped safely.';
      Exit;
    end;
    if ResultCode <> 0 then
    begin
      Result := 'The existing codemcp-remote lifecycle did not stop cleanly. Run codemcp-remote.exe stop and retry setup.';
      Exit;
    end;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssPostInstall) and WizardIsTaskSelected('addtopath') then
    AddUserPath;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    RemoveUserPath;
end;
