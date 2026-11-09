import os
import subprocess
import sys

import requests

from .config import APP_VERSION, VERSION_URL


def _version_tuple(version: str) -> tuple[int, ...]:
    parts: list[int] = []
    for part in str(version).strip().lstrip("vV").split("."):
        numeric = "".join(character for character in part if character.isdigit())
        parts.append(int(numeric or 0))
    return tuple(parts)


def check_for_update() -> str | None:
    try:
        response = requests.get(
            VERSION_URL,
            timeout=10,
            headers={"User-Agent": f"StoneLauncher/{APP_VERSION}"},
        )
        response.raise_for_status()
        data = response.json()

        remote_version = str(data["version"]).strip()
        download_url = str(data["url"]).strip()

        if _version_tuple(remote_version) > _version_tuple(APP_VERSION):
            return download_url
    except (requests.RequestException, ValueError, KeyError, TypeError):
        return None

    return None


def download_update(url: str) -> str:
    current_dir = os.path.dirname(os.path.abspath(sys.executable))
    final_path = os.path.join(current_dir, "StoneLauncher_new.exe")
    partial_path = final_path + ".part"

    response = requests.get(
        url,
        stream=True,
        timeout=120,
        headers={"User-Agent": f"StoneLauncher/{APP_VERSION}"},
    )
    response.raise_for_status()

    with open(partial_path, "wb") as file:
        for chunk in response.iter_content(chunk_size=256 * 1024):
            if chunk:
                file.write(chunk)

    os.replace(partial_path, final_path)
    return final_path


def apply_update(new_exe_path: str) -> None:
    # Never replace python.exe when the launcher is being run from source.
    if not getattr(sys, "frozen", False):
        return

    current_exe = os.path.abspath(sys.executable)
    current_dir = os.path.dirname(current_exe)
    process_name = os.path.basename(current_exe)
    batch_path = os.path.join(current_dir, "stone_update.bat")

    batch_script = f'''@echo off
setlocal
set "CURRENT_EXE={current_exe}"
set "NEW_EXE={new_exe_path}"
set "PROCESS_NAME={process_name}"

:wait_for_exit
tasklist /FI "IMAGENAME eq %PROCESS_NAME%" 2>nul | find /I "%PROCESS_NAME%" >nul
if not errorlevel 1 (
    timeout /t 1 /nobreak >nul
    goto wait_for_exit
)

copy /Y "%NEW_EXE%" "%CURRENT_EXE%" >nul
if errorlevel 1 (
    timeout /t 1 /nobreak >nul
    goto wait_for_exit
)

del /F /Q "%NEW_EXE%" >nul 2>&1
start "" "%CURRENT_EXE%"
endlocal
del /F /Q "%~f0"
'''

    with open(batch_path, "w", encoding="utf-8") as file:
        file.write(batch_script)

    subprocess.Popen(
        ["cmd.exe", "/c", "start", "", "/min", batch_path],
        cwd=current_dir,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    os._exit(0)


def run_update_check(window=None) -> None:
    # Auto-replacement is only supported by the packaged executable.
    if not getattr(sys, "frozen", False):
        return

    url = check_for_update()
    if not url:
        return

    if window:
        window.restart_signal.emit("Downloading update…")

    try:
        executable = download_update(url)
    except (requests.RequestException, OSError):
        if window:
            window.restart_signal.emit("Update download failed. Starting normally.")
        return

    if window:
        window.restart_signal.emit("Update downloaded. Restarting…")
        window.do_restart_signal.emit(executable)
    else:
        apply_update(executable)
