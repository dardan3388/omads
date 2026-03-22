param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$OmadsArgs
)

$ErrorActionPreference = "Stop"
$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $RootDir

function Invoke-BootstrapPython {
    param(
        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]]$Args
    )

    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3 @Args
        return
    }

    if (Get-Command python -ErrorAction SilentlyContinue) {
        & python @Args
        return
    }

    throw "Python 3.11+ is required but neither 'py' nor 'python' was found in PATH."
}

if (-not (Test-Path ".venv")) {
    Write-Host "Creating local virtual environment in .venv..."
    Invoke-BootstrapPython -m venv .venv
}

$venvPython = Join-Path $RootDir ".venv\Scripts\python.exe"
$venvOmads = Join-Path $RootDir ".venv\Scripts\omads.exe"

if (-not (Test-Path $venvPython)) {
    throw "The local virtual environment is missing python.exe. Recreate .venv and try again."
}

if (-not (Test-Path $venvOmads)) {
    Write-Host "Installing OMADS into the local virtual environment..."
    & $venvPython -m pip install -e .
}

& $venvOmads gui @OmadsArgs
