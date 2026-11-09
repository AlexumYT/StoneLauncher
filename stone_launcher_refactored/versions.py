# ---------------------------------------------------------------------------
ALLOWED_VERSIONS = [
    {"id": "1.21.5", "label": "1.21.5", "tags": ["LATEST"]},
    {"id": "1.20.1", "label": "1.20.1", "tags": ["POPULAR", "HAS CUSTOM CLIENT"]},
    {"id": "1.19.2", "label": "1.19.2", "tags": ["STABLE"]},
    {"id": "1.18.2", "label": "1.18.2", "tags": []},
    {"id": "1.17.1", "label": "1.17.1", "tags": []},
    {"id": "1.16.5", "label": "1.16.5", "tags": ["PVP"]},
]

UNSUPPORTED_VERSIONS = [
    {"id": "26.1.2", "label": "26.1",   "tags": ["FABRIC", "LIMITED MODS"]},
    {"id": "1.21.11","label": "1.21.11","tags": ["FABRIC", "LIMITED MODS"]},
    {"id": "1.8.9",  "label": "1.8.9",  "tags": ["FORGE", "OPTIFINE ONLY"]},
]

VERSION_ALIASES = {
    "26.1": "26.1.2",
}


def normalize_version_id(version_id: str) -> str:
    return VERSION_ALIASES.get(version_id, version_id)


def version_label(version_id: str) -> str:
    normalized = normalize_version_id(version_id)
    for version in ALLOWED_VERSIONS + UNSUPPORTED_VERSIONS:
        if version["id"] == normalized:
            return str(version["label"])
    return normalized


VERSION_BG_COLORS = {
    "1.21.5":  ("#0f3460", "#16213e"),
    "1.20.1":  ("#1a4731", "#0d2b1e"),
    "1.19.2":  ("#2a1c44", "#150e22"),
    "1.18.2":  ("#2d1b4e", "#1a0f2e"),
    "1.17.1":  ("#1a3a4a", "#0d1f2a"),
    "1.16.5":  ("#3a2000", "#1f1200"),
    "26.1.2":  ("#1a2a1a", "#0d1a0d"),
    "1.21.11": ("#2a2a1a", "#1a1a0d"),
    "1.8.9":   ("#1a1a1a", "#0d0d0d"),
}

