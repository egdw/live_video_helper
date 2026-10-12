@echo off
chcp 65001 >nul
set "URL=http://127.0.0.1:8765/player"
set "PROFILE=%LOCALAPPDATA%\TencentTrainingWeb_AutoPlayer"
set "BROWSER="
if exist "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" set "BROWSER=%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"
if exist "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe" set "BROWSER=%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"
if exist "%LocalAppData%\Microsoft\Edge\Application\msedge.exe" set "BROWSER=%LocalAppData%\Microsoft\Edge\Application\msedge.exe"
if not defined BROWSER if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" set "BROWSER=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if not defined BROWSER if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" set "BROWSER=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
if not defined BROWSER (
  echo 找不到Edge或Chrome，改用系统默认浏览器。请在播放页右上角手动点击“启用声音”。
  start "" "%URL%"
) else (
  echo 使用专用独立浏览器配置打开播放窗口，启用有声自动播放。
  echo 请保持这个窗口在前台或至少不最小化，腾讯会议共享该窗口。
  start "" "%BROWSER%" --user-data-dir="%PROFILE%" --autoplay-policy=no-user-gesture-required --app="%URL%"
)
