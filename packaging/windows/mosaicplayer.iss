; Inno Setup script for the Windows installer.
; Build the app first (pyinstaller packaging\mosaicplayer.spec), then:
;   iscc /DAppVersion=0.3.0 packaging\windows\mosaicplayer.iss
; The installer is written to dist\MosaicPlayer-<version>-windows-setup.exe.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6E1C2B9A-4F3D-4C8E-9A57-3D2F8B1E7C40}
AppName=MosaicPlayer
AppVersion={#AppVersion}
AppPublisher=itsui
AppPublisherURL=https://github.com/itsui/mosaicplayer
AppSupportURL=https://github.com/itsui/mosaicplayer/issues
DefaultDirName={autopf}\MosaicPlayer
DefaultGroupName=MosaicPlayer
DisableProgramGroupPage=yes
LicenseFile=..\..\LICENSE
OutputDir=..\..\dist
OutputBaseFilename=MosaicPlayer-{#AppVersion}-windows-setup
SetupIconFile=mosaicplayer.ico
UninstallDisplayIcon={app}\MosaicPlayer.exe
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Per-user install without admin rights is offered too.
PrivilegesRequiredOverridesAllowed=dialog
ChangesAssociations=yes
WizardStyle=modern

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "gplsassoc"; Description: "Open .gpls playlists with MosaicPlayer"; GroupDescription: "File types:"

[Files]
Source: "..\..\dist\MosaicPlayer\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\MosaicPlayer"; Filename: "{app}\MosaicPlayer.exe"
Name: "{group}\Uninstall MosaicPlayer"; Filename: "{uninstallexe}"
Name: "{autodesktop}\MosaicPlayer"; Filename: "{app}\MosaicPlayer.exe"; Tasks: desktopicon

[Registry]
; .gpls playlists (shared with GridPlayer, so only when chosen)
Root: HKA; Subkey: "Software\Classes\.gpls\OpenWithProgids"; ValueType: string; ValueName: "MosaicPlayer.gpls"; ValueData: ""; Flags: uninsdeletevalue; Tasks: gplsassoc
Root: HKA; Subkey: "Software\Classes\MosaicPlayer.gpls"; ValueType: string; ValueName: ""; ValueData: "MosaicPlayer playlist"; Flags: uninsdeletekey; Tasks: gplsassoc
Root: HKA; Subkey: "Software\Classes\MosaicPlayer.gpls\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\MosaicPlayer.exe,0"; Tasks: gplsassoc
Root: HKA; Subkey: "Software\Classes\MosaicPlayer.gpls\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\MosaicPlayer.exe"" ""%1"""; Tasks: gplsassoc
; "Open with" for videos and folders, without taking over their default app
Root: HKA; Subkey: "Software\Classes\Applications\MosaicPlayer.exe"; ValueType: string; ValueName: "FriendlyAppName"; ValueData: "MosaicPlayer"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\Applications\MosaicPlayer.exe\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\MosaicPlayer.exe"" ""%1"""
Root: HKA; Subkey: "Software\Classes\Applications\MosaicPlayer.exe\SupportedTypes"; ValueType: string; ValueName: ".mp4"; ValueData: ""
Root: HKA; Subkey: "Software\Classes\Applications\MosaicPlayer.exe\SupportedTypes"; ValueType: string; ValueName: ".mkv"; ValueData: ""
Root: HKA; Subkey: "Software\Classes\Applications\MosaicPlayer.exe\SupportedTypes"; ValueType: string; ValueName: ".webm"; ValueData: ""
Root: HKA; Subkey: "Software\Classes\Applications\MosaicPlayer.exe\SupportedTypes"; ValueType: string; ValueName: ".mov"; ValueData: ""
Root: HKA; Subkey: "Software\Classes\Applications\MosaicPlayer.exe\SupportedTypes"; ValueType: string; ValueName: ".avi"; ValueData: ""
Root: HKA; Subkey: "Software\Classes\Applications\MosaicPlayer.exe\SupportedTypes"; ValueType: string; ValueName: ".gpls"; ValueData: ""
Root: HKA; Subkey: "Software\Classes\Directory\shell\MosaicPlayer"; ValueType: string; ValueName: ""; ValueData: "Play with MosaicPlayer"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\Directory\shell\MosaicPlayer"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\MosaicPlayer.exe,0"
Root: HKA; Subkey: "Software\Classes\Directory\shell\MosaicPlayer\command"; ValueType: string; ValueName: ""; ValueData: """{app}\MosaicPlayer.exe"" ""%1"""

[Run]
Filename: "{app}\MosaicPlayer.exe"; Description: "{cm:LaunchProgram,MosaicPlayer}"; Flags: nowait postinstall skipifsilent
