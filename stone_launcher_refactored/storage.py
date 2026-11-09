import json
import os
from .config import DATA_DIR

class AccountStore:
    path = os.path.join(DATA_DIR, "account.json")
    @classmethod
    def save(cls, info: dict) -> None:
        with open(cls.path, "w", encoding="utf-8") as file:
            json.dump(info, file, indent=2)
    @classmethod
    def load(cls) -> dict | None:
        if not os.path.exists(cls.path): return None
        with open(cls.path, "r", encoding="utf-8") as file: return json.load(file)
    @classmethod
    def clear(cls) -> None:
        if os.path.exists(cls.path): os.remove(cls.path)

class PreferencesStore:
    path = os.path.join(DATA_DIR, "prefs.json")
    defaults = {"version": "1.20.1", "shaders": False}
    @classmethod
    def save(cls, version_id: str, shaders_enabled: bool) -> None:
        with open(cls.path, "w", encoding="utf-8") as file:
            json.dump({"version": version_id, "shaders": shaders_enabled}, file, indent=2)
    @classmethod
    def load(cls) -> dict:
        if not os.path.exists(cls.path): return dict(cls.defaults)
        try:
            with open(cls.path, "r", encoding="utf-8") as file: data = json.load(file)
            return {"version": data.get("version", "1.20.1"), "shaders": data.get("shaders", False)}
        except Exception:
            return dict(cls.defaults)
