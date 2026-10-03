param([string]$OutputDirectory = "dist", [switch]$Test)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$outputRoot = [IO.Path]::GetFullPath((Join-Path $projectRoot $OutputDirectory))
if (-not $outputRoot.StartsWith($projectRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'Output must stay inside the project directory.' }
New-Item -ItemType Directory -Path $outputRoot -Force | Out-Null
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $compiler)) { $compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319\csc.exe' }
if (-not (Test-Path -LiteralPath $compiler)) { throw '.NET Framework C# compiler is unavailable.' }
$core = Join-Path $projectRoot 'launcher\LauncherCore.cs'
$program = Join-Path $projectRoot 'launcher\Program.cs'
$assembly = Join-Path $projectRoot 'launcher\AssemblyInfo.cs'
$icon = Join-Path $projectRoot 'launcher\vault.ico'
$manifest = Join-Path $projectRoot 'launcher\app.manifest'
$binary = Join-Path $outputRoot 'GenMediaVault.exe'
& $compiler /nologo /target:winexe /platform:anycpu /optimize+ /utf8output "/out:$binary" "/win32icon:$icon" "/win32manifest:$manifest" /reference:System.Windows.Forms.dll /reference:System.Drawing.dll $core $program $assembly
if ($LASTEXITCODE -ne 0) { throw 'Launcher compilation failed.' }
Write-Output "Built $binary"
if ($Test) {
    $testBinary = Join-Path $outputRoot 'LauncherTests.exe'
    & $compiler /nologo /target:exe /platform:anycpu /utf8output "/out:$testBinary" $core (Join-Path $projectRoot 'launcher\LauncherTests.cs')
    if ($LASTEXITCODE -ne 0) { throw 'Launcher test compilation failed.' }
    & $testBinary
    if ($LASTEXITCODE -ne 0) { throw 'Launcher tests failed.' }
}
