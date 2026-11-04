; Instalador do Fervura (Inno Setup 6).
; Gerado por build.bat depois do PyInstaller; os caminhos são relativos a esta pasta.

#define AppName "Fervura"
#define AppVersion "1.0.0"
#define AppExe "Fervura.exe"
#define PawnIOVersion "2.2.0"

[Setup]
AppId={{49026729-6B1B-48E0-BE60-D5E0C6352593}
AppName={#AppName}
AppVersion={#AppVersion}
DefaultDirName={autopf}\Fervura
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputDir=..\dist
OutputBaseFilename=Fervura-Setup-{#AppVersion}
SetupIconFile=..\build\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
InfoBeforeFile=..\build\componentes.txt
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
AppMutex=FervuraMutex
CloseApplications=yes
SetupLogging=yes

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\Fervura\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Instalador oficial do PawnIO, sem modificações: fica no programa (reinstalação) e é
; extraído para a pasta temporária durante esta instalação.
Source: "..\build\vendor\pawnio\PawnIO_setup.exe"; DestDir: "{app}\third_party\PawnIO"; Flags: ignoreversion
Source: "..\build\vendor\pawnio\PawnIO_setup.exe"; Flags: dontcopy
Source: "..\build\THIRD_PARTY_NOTICES.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[UninstallRun]
; Remove a tarefa agendada de "Iniciar com o Windows", se o usuário a tiver ativado.
Filename: "{sys}\schtasks.exe"; Parameters: "/Delete /TN ""Fervura"" /F"; Flags: runhidden; RunOnceId: "RemoveAutostartTask"

[Code]
const
  PawnIOKey = 'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\PawnIO';

var
  ProgressPage: TOutputProgressWizardPage;

function PawnIOVersion(var Version: String): Boolean;
begin
  Result := RegQueryStringValue(HKLM64, PawnIOKey, 'DisplayVersion', Version) or
            RegQueryStringValue(HKLM32, PawnIOKey, 'DisplayVersion', Version);
end;

{ Mesmo critério do LibreHardwareMonitor: versão 2.0 ou mais nova. }
function PawnIOInstalled: Boolean;
var
  Version: String;
  Dot: Integer;
begin
  Result := False;
  if PawnIOVersion(Version) then
  begin
    Dot := Pos('.', Version);
    if Dot > 0 then
      Result := StrToIntDef(Copy(Version, 1, Dot - 1), 0) >= 2
    else
      Result := StrToIntDef(Version, 0) >= 2;
  end;
end;

procedure InitializeWizard;
begin
  ProgressPage := CreateOutputProgressPage('Driver PawnIO',
    'Instalando o driver PawnIO {#PawnIOVersion} (software de terceiros, namazso - pawnio.eu).');
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Version: String;
  ResultCode: Integer;
  Setup: String;
begin
  Result := '';
  if PawnIOInstalled then
  begin
    PawnIOVersion(Version);
    Log('PawnIO ' + Version + ' já está instalado; nada a fazer.');
    Exit;
  end;

  if MsgBox('O driver PawnIO não está instalado neste computador (ou está desatualizado).' + #13#10#13#10 +
            'Ele é necessário para que o Fervura leia a temperatura da CPU.' + #13#10#13#10 +
            'Será executado o instalador oficial do PawnIO {#PawnIOVersion}, de namazso (https://pawnio.eu), ' +
            'sem modificações.' + #13#10#13#10 + 'Deseja instalar o driver PawnIO?',
            mbConfirmation, MB_YESNO) <> IDYES then
  begin
    Log('Usuário recusou a instalação do PawnIO.');
    Result := 'Não é possível continuar a instalação sem o driver PawnIO.';
    Exit;
  end;

  ProgressPage.SetText('Extraindo PawnIO_setup.exe...', '');
  ProgressPage.SetProgress(0, 3);
  ProgressPage.Show;
  try
    ExtractTemporaryFile('PawnIO_setup.exe');
    Setup := ExpandConstant('{tmp}\PawnIO_setup.exe');
    ProgressPage.SetText('Executando o instalador oficial do PawnIO {#PawnIOVersion}...',
                         Setup + ' -install');
    ProgressPage.SetProgress(1, 3);
    Log('Executando: ' + Setup + ' -install');
    if not Exec(Setup, '-install', '', SW_SHOW, ewWaitUntilTerminated, ResultCode) then
    begin
      Result := 'Não foi possível executar o instalador do PawnIO: ' + SysErrorMessage(ResultCode);
      Exit;
    end;
    Log('Instalador do PawnIO terminou com código ' + IntToStr(ResultCode));
    ProgressPage.SetText('Verificando a instalação do PawnIO...', '');
    ProgressPage.SetProgress(2, 3);
    if not PawnIOInstalled then
    begin
      Result := 'O driver PawnIO não foi instalado (código ' + IntToStr(ResultCode) + '). ' +
                'Não é possível continuar a instalação sem o driver PawnIO.';
      Exit;
    end;
    PawnIOVersion(Version);
    Log('PawnIO ' + Version + ' instalado.');
    ProgressPage.SetProgress(3, 3);
  finally
    ProgressPage.Hide;
  end;
end;
