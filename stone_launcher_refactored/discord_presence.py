import threading
import time
try:
    from pypresence import Presence
except ImportError:
    Presence = None
from .config import APP_VERSION, DISCORD_APPLICATION_ID, DISCORD_LARGE_IMAGE, DISCORD_WEBSITE_URL

def _discard_log(*_args, **_kwargs) -> None:
    pass

class DiscordRichPresence:
    """Thread-safe Discord RPC wrapper with logging and safe fallbacks."""

    def __init__(self, application_id: str):
        self.application_id = str(application_id).strip()
        self.rpc = None
        self.connected = False
        self._lock = threading.RLock()
        self._launcher_started = int(time.time())

    def connect(self, force: bool = False) -> bool:
        if Presence is None:
            _discard_log("[discord] pypresence is not installed")
            return False
        if not self.application_id.isdigit():
            _discard_log(f"[discord] Invalid Application ID: {self.application_id!r}")
            return False

        with self._lock:
            if self.connected and not force:
                return True
            if force:
                self._disconnect_locked(clear=False)
            try:
                _discard_log(f"[discord] Connecting with app ID {self.application_id}...")
                self.rpc = Presence(client_id=self.application_id)
                self.rpc.connect()
                self.connected = True
                _discard_log("[discord] IPC connected successfully")
                return True
            except Exception as exc:
                self.rpc = None
                self.connected = False
                _discard_log(f"[discord] Connection failed: {type(exc).__name__}: {exc!r}")
                return False

    def _disconnect_locked(self, clear: bool = True) -> None:
        if self.rpc is not None:
            try:
                if clear:
                    self.rpc.clear()
            except Exception:
                pass
            try:
                self.rpc.close()
            except Exception:
                pass
        self.rpc = None
        self.connected = False

    def _send(self, payload: dict, label: str) -> bool:
        if not self.connect():
            return False
        with self._lock:
            try:
                _discard_log(f"[discord] Sending {label}: {payload}")
                result = self.rpc.update(**payload)
                _discard_log(f"[discord] {label} accepted by Discord: {result!r}")
                return True
            except Exception as exc:
                _discard_log(f"[discord] {label} failed: {type(exc).__name__}: {exc!r}")
                self._disconnect_locked(clear=False)
                return False

    def show_launcher(self, version_id: str | None = None) -> None:
        # Send a minimal payload first. This rules out bad image assets or buttons.
        minimal = {
            "details": "Competitive",
            "state": "Playing Solo",
            "start": self._launcher_started,
        }
        if not self._send(minimal, "minimal launcher presence"):
            return

        # Then enrich it. If stone_logo is missing, Discord still keeps the minimal status.
        rich = {
            "details": f"Minecraft {version_id}" if version_id else "Choosing a version",
            "state": "In the launcher",
            "start": self._launcher_started,
            "large_image": DISCORD_LARGE_IMAGE,
            "large_text": f"Stone Launcher v{APP_VERSION}",
        }
        if DISCORD_WEBSITE_URL.startswith(("http://", "https://")):
            rich["buttons"] = [{"label": "Stone Launcher", "url": DISCORD_WEBSITE_URL}]
        self._send(rich, "full launcher presence")

    def show_playing(self, version_id: str, username: str) -> None:
        payload = {
            "details": f"Playing Minecraft {version_id}",
            "state": f"Playing as {username}",
            "start": int(time.time()),
            "large_image": DISCORD_LARGE_IMAGE,
            "large_text": f"Stone Launcher v{APP_VERSION}",
        }
        if not self._send(payload, "playing presence"):
            # Retry without optional fields in case the asset/party configuration is invalid.
            self.connect(force=True)
            self._send({
                "details": f"Playing Minecraft {version_id}",
                "state": f"Playing as {username}",
                "start": int(time.time()),
            }, "minimal playing presence")

    def close(self) -> None:
        with self._lock:
            self._disconnect_locked(clear=True)
            _discard_log("[discord] Rich Presence closed")

discord_rpc = DiscordRichPresence(DISCORD_APPLICATION_ID)

discord_rpc = DiscordRichPresence(DISCORD_APPLICATION_ID)
