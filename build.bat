@echo off
rem Gera dist\Fervura\ (PyInstaller) e dist\Fervura-Setup-*.exe (Inno Setup).
rem Requisitos na maquina de build: Python 3.12 64-bit (py launcher) e Inno Setup 6.
setlocal
cd /d "%~dp0"

if not defined PY set "PY=py -3.12"

%PY% --version >nul 2>&1
if errorlevel 1 (
    echo Python nao encontrado pelo comando: %PY%
    echo Instale com: py install 3.12
    echo Ou escolha outra versao antes do build, por exemplo: set PY=py -3.13
    goto :fail
)

if not exist .venv-build\Scripts\python.exe (
    echo === Criando ambiente de build .venv-build com %PY%
    %PY% -m venv .venv-build || goto :fail
)
set "VPY=.venv-build\Scripts\python.exe"

echo === Instalando dependencias de build
%VPY% -m pip install --upgrade pip || goto :fail
%VPY% -m pip install --upgrade -r requirements-build.txt || goto :fail

echo === Testes
%VPY% -m unittest tests\test_protocol.py tests\test_app.py tests\test_datadir.py || goto :fail

echo === Baixando LibreHardwareMonitorLib e PawnIO, gerando listas
%VPY% tools\prepare_build.py || goto :fail

echo === PyInstaller
%VPY% -m PyInstaller --noconfirm --clean --windowed ^
    --name Fervura ^
    --icon "%CD%\build\icon.ico" ^
    --add-data "%CD%\build\vendor\lhm;lhm" ^
    --hidden-import pystray._win32 ^
    --uac-admin ^
    --collect-all pythonnet ^
    --collect-all clr_loader ^
    --exclude-module setuptools ^
    --distpath dist --workpath build\pyinstaller --specpath build ^
    iniciar.pyw || goto :fail

echo === Inno Setup
set "ISCC="
for %%I in (ISCC.exe) do set "ISCC=%%~$PATH:I"
if not defined ISCC if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not defined ISCC (
    echo ISCC.exe do Inno Setup 6 nao encontrado.
    goto :fail
)
"%ISCC%" installer\Fervura.iss || goto :fail

echo.
echo === Pronto: veja a pasta dist\
exit /b 0

:fail
echo.
echo === FALHOU (veja a mensagem acima)
exit /b 1
