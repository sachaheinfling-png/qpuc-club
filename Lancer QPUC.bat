@echo off
chcp 65001 >nul
title QPUC Club - laissez cette fenetre ouverte
cd /d "%~dp0"
where py >nul 2>nul && (py -3 qpuc.py & goto fin)
where python >nul 2>nul && (python qpuc.py & goto fin)
echo.
echo   Python n'est pas installe sur ce PC.
echo   1. Ouvrez https://www.python.org/downloads/ et installez Python
echo      (cochez bien "Add python.exe to PATH" pendant l'installation).
echo   2. Relancez ce fichier.
echo.
start https://www.python.org/downloads/
:fin
pause
