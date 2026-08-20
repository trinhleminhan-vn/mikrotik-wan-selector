@echo off
REM ====================================================================
REM  ChonNhaMang.cmd - bam doi de mo tool chon nha mang (Viettel / VNPT)
REM  Tu dong go chan file, xin quyen Administrator va mo giao dien.
REM ====================================================================
chcp 65001 >nul 2>&1
setlocal
set "PS1=%~dp0WanSwitch.ps1"

if not exist "%PS1%" (
    echo Khong tim thay file WanSwitch.ps1 canh file nay.
    echo Hay giu nguyen ca thu muc khi chep di.
    pause
    exit /b 1
)

REM File chep tu may khac / tai ve co the bi Windows danh dau chan (Mark of the Web)
powershell -NoProfile -Command "Get-ChildItem -LiteralPath '%~dp0' -Recurse -File | Unblock-File -ErrorAction SilentlyContinue" >nul 2>&1

powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%" %*
if errorlevel 1 pause
endlocal
