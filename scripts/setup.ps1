# Windows wrapper: runs setup.sh with Git Bash.
$bash = Join-Path ${env:ProgramFiles} "Git\bin\bash.exe"
if (-not (Test-Path $bash)) { Write-Error "Git Bash not found at $bash"; exit 1 }
& $bash (Join-Path $PSScriptRoot "setup.sh")
