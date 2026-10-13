@echo off
rem Windows side of the MacPatch: takes it back out, so the code is again what was verified on
rem Windows. What the MacPatch changes: macpatch\MACPATCH.md. Needs Git for Windows on PATH.
rem
rem   macpatch\windows.cmd            revert the MacPatch (nothing happens if it is not applied)
rem   macpatch\windows.cmd --status   applied / not applied
setlocal
cd /d "%~dp0.."
set "PATCH=macpatch/mac.patch"
set "GA=git apply --whitespace=nowarn"

where git >nul 2>&1 || (echo git is not on PATH; install Git for Windows first. & exit /b 1)

%GA% --reverse --check "%PATCH%" >nul 2>&1 && (set "STATE=applied") || (set "STATE=other")
if "%STATE%"=="other" (
  %GA% --check "%PATCH%" >nul 2>&1 && (set "STATE=not-applied") || (set "STATE=edited")
)

if /i "%~1"=="--status" goto status
if not "%~1"=="" (echo unknown option: %~1 & exit /b 2)

if "%STATE%"=="not-applied" (
  echo MacPatch is not applied; nothing to revert.
  exit /b 0
)
if "%STATE%"=="edited" (
  echo The patched lines were edited since the MacPatch was applied, so it cannot be reverted
  echo automatically. Undo the changes listed in macpatch\MACPATCH.md by hand.
  exit /b 1
)
%GA% --reverse "%PATCH%" || (echo Revert failed; see macpatch\MACPATCH.md. & exit /b 1)
echo MacPatch reverted; the code is back to the Windows version:
%GA% --stat "%PATCH%"
echo If the MacPatch had been committed, commit this revert too.
exit /b 0

:status
if "%STATE%"=="applied" echo applied
if "%STATE%"=="not-applied" echo not applied
if "%STATE%"=="edited" echo not applied cleanly either way; the patched lines were edited, see macpatch\MACPATCH.md
exit /b 0
