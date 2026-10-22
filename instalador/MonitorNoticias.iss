; Instalador do Monitor de notícias para Windows (Inno Setup 6).
; Gerado por "python empacotar.py" depois do PyInstaller; a versão vem de /DMyAppVersion.
;
; Por padrão instala só para o usuário atual (sem pedir administrador), assim a
; atualização automática roda sem perguntas. No assistente dá para escolher
; "para todos os usuários" (Arquivos de Programas). Os dados ficam sempre em
; %LOCALAPPDATA%\MonitorNoticias e não são apagados ao desinstalar.

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif
#define MyAppName "Monitor de notícias"
#define MyAppExe "MonitorNoticias.exe"

[Setup]
AppId={{E7278209-91F4-4384-971E-50519CC7C9D1}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher=Nerqs
AppPublisherURL=https://github.com/Nerqs/monitor-noticias
AppSupportURL=https://github.com/Nerqs/monitor-noticias/issues
AppUpdatesURL=https://github.com/Nerqs/monitor-noticias/releases
VersionInfoVersion={#MyAppVersion}
DefaultDirName={autopf}\MonitorNoticias
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog commandline
WizardStyle=modern
SetupIconFile=..\assets\icone.ico
UninstallDisplayIcon={app}\{#MyAppExe}
UninstallDisplayName={#MyAppName}
OutputDir=..\dist
OutputBaseFilename=MonitorNoticias-instalador-windows
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
CloseApplications=force
RestartApplications=no

[Languages]
Name: "ptbr"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "Criar atalho na área de trabalho"; GroupDescription: "Atalhos:"

[Files]
Source: "..\dist\MonitorNoticias\*"; DestDir: "{app}"; Excludes: "\data,\data\*"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; Arquivos da versão anterior que não existem mais na nova não ficam para trás.
Type: filesandordirs; Name: "{app}\_internal"

[INI]
; Marca usada pelo programa para saber que está instalado (dados em LOCALAPPDATA).
Filename: "{app}\instalacao.ini"; Section: "instalacao"; Key: "versao"; String: "{#MyAppVersion}"

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; Comment: "Notícias de tecnologia e falhas críticas"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; Tasks: desktopicon; Comment: "Notícias de tecnologia e falhas críticas"

[Registry]
; "Iniciar com o sistema" grava este valor pelo programa; a desinstalação apaga.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "MonitorNoticias"; Flags: dontcreatekey uninsdeletevalue

[Run]
; Também roda na atualização silenciosa: o programa reabre sozinho na versão nova.
Filename: "{app}\{#MyAppExe}"; Description: "Abrir o {#MyAppName}"; Flags: nowait postinstall runasoriginaluser; Check: AbrirDepois

[UninstallRun]
Filename: "schtasks.exe"; Parameters: "/Delete /F /TN MonitorNoticias"; Flags: runhidden; RunOnceId: "TarefaColeta"
Filename: "schtasks.exe"; Parameters: "/Delete /F /TN MonitorNoticias-residente"; Flags: runhidden; RunOnceId: "TarefaAlertas"

[UninstallDelete]
Type: files; Name: "{app}\instalacao.ini"

[Code]
// /SEMABRIR=1 instala sem abrir o programa no fim (usado nos testes do CI).
function AbrirDepois: Boolean;
begin
  Result := ExpandConstant('{param:SEMABRIR|0}') = '0';
end;
