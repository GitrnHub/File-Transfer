# BaiduNetdisk 8.9.2.101 portable launcher v2.
# No elevation, registration, installer, service installation or binary patch.
[CmdletBinding()]
param([ValidateSet('Portable','Compatibility','Diagnose')][string]$Mode='Portable')
$ErrorActionPreference='Stop'
$root=Split-Path -Parent $MyInvocation.MyCommand.Path
$app=Join-Path $root 'App'
$data=Join-Path $root 'Data'
$logdir=Join-Path $data 'Logs'
$exe=Join-Path $app 'BaiduNetdisk.exe'
$script:logfile=$null
$script:failed=New-Object 'System.Collections.Generic.List[string]'
function Note([string]$message){
  Write-Host $message
  if($script:logfile){try{[IO.File]::AppendAllText($script:logfile,$message+[Environment]::NewLine,[Text.Encoding]::UTF8)}catch{}}
}
function WriteCheck([string]$folder){
  $probe=Join-Path $folder ('.bdn_writable_check_'+[guid]::NewGuid().ToString('N')+'.tmp')
  try{
    $stream=[IO.File]::Open($probe,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
    try{$stream.WriteByte(0x42)}finally{$stream.Dispose()}
    [IO.File]::Delete($probe)
    return $true
  }catch{
    $script:failed.Add($folder+' : '+$_.Exception.Message)
    try{if([IO.File]::Exists($probe)){[IO.File]::Delete($probe)}}catch{}
    return $false
  }
}
try{
  if(!(Test-Path -LiteralPath $exe -PathType Leaf)){Note 'ERROR: App/BaiduNetdisk.exe not found. Extract the whole ZIP.';exit 2}
  if(!(Test-Path -LiteralPath $logdir)){New-Item -Path $logdir -ItemType Directory -Force | Out-Null}
  $script:logfile=Join-Path $logdir 'Launcher_LastRun.log'
  [IO.File]::WriteAllText($script:logfile,'Launcher started '+(Get-Date).ToString('o')+[Environment]::NewLine,[Text.Encoding]::UTF8)
  Note ('MODE='+$Mode+' ROOT='+$root)
  $identity=[Security.Principal.WindowsIdentity]::GetCurrent()
  $principal=[Security.Principal.WindowsPrincipal]::new($identity)
  if($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){
    Note 'ERROR: elevated token detected. Do not run this program as Administrator.'
    exit 3
  }
  $desktop=[Environment]::GetFolderPath([Environment+SpecialFolder]::DesktopDirectory)
  if($desktop -and $root.StartsWith($desktop,[StringComparison]::OrdinalIgnoreCase)){
    Note 'WARNING: program is on Desktop. Windows Defender Controlled Folder Access can block writes.'
    Note 'Recommended: extract to D:\PortableApps\BaiduNetdiskPortable or another ordinary writable directory.'
  }
  if($root -match '(?i)\\OneDrive\\'){Note 'WARNING: OneDrive-managed folder; use an ordinary local directory.'}
  $dirs=@($app)+@(Get-ChildItem -LiteralPath $app -Directory -Recurse -Force | ForEach-Object{$_.FullName})
  foreach($kind in @('Roaming','Local','Temp','Logs')){
    $folder=Join-Path $data $kind
    if(!(Test-Path -LiteralPath $folder)){New-Item -Path $folder -ItemType Directory -Force | Out-Null}
    $dirs+= $folder
  }
  $ok=0
  foreach($folder in $dirs){if(WriteCheck $folder){$ok++}}
  Note ('WRITE_TEST='+$ok+'/'+$dirs.Count)
  if($script:failed.Count -gt 0){
    foreach($item in $script:failed){Note ('WRITE_DENIED '+$item)}
    Note 'ERROR: client requires write access to its installation subdirectories.'
    Note 'Move the entire extracted program to a writable folder outside Desktop/OneDrive. Do NOT grant admin rights.'
    exit 4
  }
  if($Mode -eq 'Diagnose'){Note 'DIAGNOSE_OK: app was NOT launched.';exit 0}
  if($Mode -eq 'Portable'){
    # Keep the genuine USERPROFILE and Windows identity / shell known folders.
    $env:APPDATA=Join-Path $data 'Roaming'
    $env:LOCALAPPDATA=Join-Path $data 'Local'
    $env:TEMP=Join-Path $data 'Temp'
    $env:TMP=$env:TEMP
    Note 'DATA_MODE=PORTABLE_APPDATA; USERPROFILE is unchanged.'
  } else {
    Note 'DATA_MODE=WINDOWS_DEFAULT; APPDATA/LOCALAPPDATA unchanged.'
  }
  $p=Start-Process -FilePath $exe -WorkingDirectory $app -PassThru -ErrorAction Stop
  Note ('STARTED_PID='+$p.Id)
  Start-Sleep -Seconds 17
  $p.Refresh()
  if($p.HasExited){Note ('MAIN_EXIT_CODE='+$p.ExitCode);exit 5}
  Note 'MAIN_RUNNING=true'
  $child=@(Get-CimInstance Win32_Process -Filter "Name='BaiduNetdiskUnite.exe'" -ErrorAction SilentlyContinue |
    Where-Object{$_.ParentProcessId -eq $p.Id})
  if($child.Count -gt 0){Note ('BROWSER_ENGINE_CHILDREN='+$child.Count)}
  else{Note 'WARNING: browser engine child was not observed. If UI is incomplete, try Run_Compatibility.cmd.'}
  Note 'Startup smoke test passed; login and file transfers are not automatically validated.'
  exit 0
}catch{
  Note ('ERROR: '+$_.Exception.ToString())
  exit 6
}
