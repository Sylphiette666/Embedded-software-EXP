param([string]$Port)
$ErrorActionPreference='Stop'
if (-not $Port) {
    $labPorts=@(Get-CimInstance Win32_SerialPort | Where-Object { $_.Name -match 'CH340|CH341|USB-SERIAL' })
    if ($labPorts.Count -eq 1) { $Port=$labPorts[0].DeviceID }
    elseif ($labPorts.Count -gt 1) { throw 'Multiple CH340/CH341 ports found. Run with -Port COMn.' }
    else { throw 'No CH340/CH341 port found. Connect the BASEBOARD USB1 data cable and turn power on.' }
}
if ($Port -notmatch '^COM([1-9][0-9]*)$') { throw 'Use a port name such as COM3.' }
$labNumber=$Matches[1]
$labTerminal='D:\TeraTerm\ttermpro.exe'
$labProfile=Join-Path $PSScriptRoot 'F103_TeraTerm.ini'
$labArgs='/C={0} /BAUD=115200 /CDATABIT=8 /CPARITY=none /CSTOPBIT=1 /CFLOWCTRL=none /KR=UTF8 /KT=UTF8 /F="{1}"' -f $labNumber,$labProfile
# Interactive terminal requested by the user; this window is intentionally visible.
Start-Process -FilePath $labTerminal -ArgumentList $labArgs -WorkingDirectory (Split-Path $labTerminal)
