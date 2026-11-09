import os
import subprocess
import threading
from PyQt6.QtCore import QThread, pyqtSignal
from minecraft_launcher_lib import command
from .config import MC_DIR
from .discord_presence import discord_rpc
from .minecraft_service import (
    OPTIFINE_FILENAME,
    download_assets,
    download_client,
    download_libraries,
    download_launcher_mods,
    download_natives,
    download_optifine,
    ensure_java,
    get_version_data,
    install_fabric,
    install_forge,
    prune_managed_mods,
    setup_directories,
)


class LaunchThread(QThread):
    status = pyqtSignal(str)
    progress = pyqtSignal(int)
    minecraft_started = pyqtSignal()
    minecraft_stopped = pyqtSignal()

    def __init__(self, version: dict, account_info: dict, shaders_enabled: bool = False):
        super().__init__()
        self.version = version
        self.account_info = account_info
        self.shaders_enabled = shaders_enabled

    def _emit_cmd(self, launch_version_id: str, java_exec: str) -> None:
        cmd = command.get_minecraft_command(
            launch_version_id,
            MC_DIR,
            {
                "username": self.account_info["username"],
                "uuid": self.account_info["uuid"],
                "token": self.account_info["access_token"],
                # Natives are extracted here by download_natives(); point the
                # JVM at that folder for both vanilla and Fabric launches.
                "nativesDirectory": os.path.join(MC_DIR, "natives", self.version["id"]),
            },
        )
        if not cmd:
            raise Exception("Minecraft command generation failed")
        cmd[0] = java_exec
        if not os.path.exists(cmd[0]):
            raise FileNotFoundError(f"Launch executable missing: {cmd[0]}")
        discord_rpc.show_playing(
            str(self.version.get("label", self.version["id"])),
            self.account_info["username"],
        )
        process = subprocess.Popen(
            cmd,
            cwd=MC_DIR,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        self.minecraft_started.emit()

        # Keep the launcher process alive and restore its status after Minecraft exits.
        def _watch_minecraft() -> None:
            try:
                process.wait()
            finally:
                discord_rpc.show_launcher(str(self.version.get("label", self.version["id"])))
                self.minecraft_stopped.emit()

        threading.Thread(target=_watch_minecraft, daemon=True).start()

    def run(self) -> None:
        try:
            version_entry = self.version
            version_id = version_entry["id"]
            self.progress.emit(0)

            self.status.emit("Fetching version data…")
            version_data = get_version_data(version_entry)

            self.status.emit("Setting up directories…")
            setup_directories(version_id)

            self.status.emit("Downloading client…")
            download_client(version_data, version_id, self.progress.emit)

            self.status.emit("Downloading libraries…")
            download_libraries(version_data, self.progress.emit)

            self.status.emit("Downloading natives…")
            download_natives(version_data, version_id, self.progress.emit)

            self.status.emit("Downloading assets…")
            download_assets(version_data, self.progress.emit)

            self.status.emit("Checking Java…")
            java_exec = ensure_java(version_id, status_callback=self.status.emit)

            # ── 1.8.9 path (Forge + OptiFine) ──────────────────────────────
            if version_id == "1.8.9":
                FORGE_189 = "1.8.9-11.15.1.2318-1.8.9"
                forge_id = next(
                    (
                        d
                        for d in os.listdir(os.path.join(MC_DIR, "versions"))
                        if "1.8.9" in d and "forge" in d.lower()
                    ),
                    None,
                )
                if not forge_id:
                    forge_id = install_forge(
                        version_id,
                        FORGE_189,
                        java_exec,
                        status_callback=self.status.emit,
                    )

                optifine_path = os.path.join(MC_DIR, "mods", OPTIFINE_FILENAME)
                if not os.path.exists(optifine_path):
                    self.status.emit("Downloading OptiFine…")
                    optifine_name = download_optifine()
                else:
                    optifine_name = OPTIFINE_FILENAME
                prune_managed_mods({optifine_name})

                self.progress.emit(100)
                self.status.emit("Launching 1.8.9 with Forge…")
                self._emit_cmd(forge_id, java_exec)
                self.status.emit("Minecraft launched (Forge + OptiFine)")
                return

            # ── Fabric path ─────────────────────────────────────────────────
            self.status.emit("Installing Fabric…")
            fabric_version = install_fabric(
                version_id, java_exec, status_callback=self.status.emit
            )

            self.status.emit("Syncing mods…")
            download_launcher_mods(
                version_id,
                self.shaders_enabled,
                status_callback=self.status.emit,
            )

            self.progress.emit(100)
            self.status.emit("Launching…")
            self._emit_cmd(fabric_version, java_exec)
            self.status.emit("Minecraft launched")

        except FileNotFoundError as e:
            self.status.emit(f"Missing file: {getattr(e, 'filename', None) or e}")
        except Exception as e:
            self.status.emit(f"Error: {e}")
