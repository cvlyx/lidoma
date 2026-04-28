$ErrorActionPreference = "Stop"

# IMPORTANT:
# Python 3.14 currently forces some deps (e.g. pydantic-core) to build from source and can hang at
# "Preparing metadata". Prefer Python 3.12/3.13 for a smooth install on Windows.

$py = $null
try { $py = (Get-Command py -ErrorAction Stop).Source } catch { }

$venvPython = ".\.venv\Scripts\python.exe"
if (!(Test-Path $venvPython)) {
  if ($py) {
    & py -3.14 -m venv .venv
  } else {
    python -m venv .venv
  }
}

& $venvPython -m pip install -r requirements.txt
& $venvPython -m uvicorn app:app --reload --port 8787

