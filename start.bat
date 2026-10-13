@echo off
rem ===========================================================================
rem  Agent Demo · Windows 一键启动（双击本文件即可）
rem
rem  本文件只是转发器：真正的逻辑都在同目录的 start.sh，避免维护两套实现。
rem  它需要 Git for Windows 提供的 bash；找不到时会给出明确的手动启动步骤。
rem
rem  用法：start.bat          启动
rem        start.bat check    只做环境自检
rem        start.bat stop     停止服务
rem ===========================================================================
setlocal
chcp 65001 >nul
title Agent Demo

set "ROOT=%~dp0"
rem 转成 MSYS 能识别的路径：C:\a\b\  ->  C:/a/b/
set "ROOTU=%ROOT:\=/%"

rem --- 1. 优先找 Git for Windows 自带的 bash.exe -----------------------------
rem   （含 (x86) 的路径先存进变量，避免括号在块里被当成语法）
set "PF=%ProgramFiles%"
set "PF86=%ProgramFiles(x86)%"
set "GIT_LOCAL=%LOCALAPPDATA%\Programs\Git\bin\bash.exe"

set "BASH="
if not defined BASH if exist "%PF%\Git\bin\bash.exe" set "BASH=%PF%\Git\bin\bash.exe"
if not defined BASH if exist "%PF86%\Git\bin\bash.exe" set "BASH=%PF86%\Git\bin\bash.exe"
if not defined BASH if exist "%GIT_LOCAL%" set "BASH=%GIT_LOCAL%"

rem --- 2. 退回 PATH 上的 bash（仅当它确实是 Git 自带的那个）-------------------
if not defined BASH (
  for /f "delims=" %%I in ('where bash 2^>nul') do (
    if not defined BASH (
      echo %%I | findstr /i /c:"\Git\bin" >nul && set "BASH=%%I"
    )
  )
)

if not defined BASH (
  echo.
  echo   [X] 没有找到 Git Bash —— bash.exe
  echo.
  echo   本启动脚本依赖 Git for Windows 自带的 bash。
  echo   下载安装： https://git-scm.com/download/win
  echo.
  echo   装好后重新双击本文件即可。也可以手动分两个终端启动：
  echo.
  echo     后端： cd server   然后  uv sync   然后  python main.py
  echo     前端： cd web      然后  pnpm install   然后  pnpm dev
  echo.
  pause
  exit /b 1
)

rem --- 3. 交给 start.sh ------------------------------------------------------
"%BASH%" "%ROOTU%start.sh" %*
set "CODE=%ERRORLEVEL%"

rem Ctrl+C 属于正常退出路径，只有真正出错时才停住让人看日志
if not "%CODE%"=="0" (
  echo.
  echo   退出码：%CODE%
  pause
)
exit /b %CODE%
