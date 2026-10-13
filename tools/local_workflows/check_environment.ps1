$ErrorActionPreference='Stop'
$env:PYTHONUTF8='1'
$labPython='D:\F411_PythonEnv\Scripts\python.exe'
$labRoot='D:\MDK5\F103_Labs'
& $labPython -m pip check
if ($LASTEXITCODE -ne 0) { throw 'Python dependency check failed.' }
& $labPython (Join-Path $labRoot 'tools\build_keil.py')
if ($LASTEXITCODE -ne 0) { throw 'Keil rebuild failed.' }
& $labPython (Join-Path $labRoot 'tools\check_project.py')
if ($LASTEXITCODE -ne 0) { throw 'Project check failed.' }
& $labPython (Join-Path $labRoot 'tools\check_firmware_logic.py')
if ($LASTEXITCODE -ne 0) { throw 'Software logic check failed.' }
& $labPython (Join-Path $labRoot 'tools\check_oled_logic.py')
if ($LASTEXITCODE -ne 0) { throw 'OLED software-I2C logic check failed.' }
Write-Output 'PASS: all checks completed. No board firmware was written.'
