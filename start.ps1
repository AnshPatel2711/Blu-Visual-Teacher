$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
python src\build_lesson.py --serve
