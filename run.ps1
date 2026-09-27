# Set up (first run) and start KhetOS at http://localhost:8501.
# Uses uv when it is installed (its venvs have no pip); otherwise the standard venv + pip.
# Streamlit is started with `python -m streamlit`, not the streamlit.exe shim, which uv-made venvs
# create as a trampoline that can fail on some Windows setups.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$py = ".\.venv\Scripts\python.exe"
$uv = Get-Command uv -ErrorAction SilentlyContinue

if (-not (Test-Path $py)) {
    if ($uv) { uv venv .venv --python 3.12 } else { python -m venv .venv }
    if ($LASTEXITCODE -ne 0) { throw "Could not create .venv (Python 3.12 is required)" }
}

# Install only when requirements.txt changed since the last successful install.
$stamp = ".venv\requirements.installed"
if (-not (Test-Path $stamp) -or (Get-Item requirements.txt).LastWriteTime -gt (Get-Item $stamp).LastWriteTime) {
    if ($uv) {
        uv pip install --python $py -r requirements.txt
    } else {
        & $py -m pip --version *> $null
        if ($LASTEXITCODE -ne 0) { & $py -m ensurepip --upgrade }
        & $py -m pip install -r requirements.txt
    }
    if ($LASTEXITCODE -ne 0) { throw "Installing requirements failed" }
    Set-Content -Path $stamp -Value (Get-Date -Format o)
}

& $py -m streamlit run app.py
