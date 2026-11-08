@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Fabrication de QPUC_Club.exe (necessite Internet, une seule fois)...
py -3 -m pip install --upgrade pyinstaller || python -m pip install --upgrade pyinstaller
py -3 -m PyInstaller --noconfirm --onefile --name QPUC_Club --add-data "web;web" qpuc.py || python -m PyInstaller --noconfirm --onefile --name QPUC_Club --add-data "web;web" qpuc.py
copy /y dist\QPUC_Club.exe . >nul && echo. && echo Termine : QPUC_Club.exe est pret. Il peut etre copie seul sur une cle USB.
pause
