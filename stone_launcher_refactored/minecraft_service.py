import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor

import minecraft_launcher_lib.runtime
import requests
from minecraft_launcher_lib import forge as mc_forge

from .config import (
    APP_ROOT,
    BASE_DIR,
    DATA_DIR,
    JAVA_MAJOR,
    JAVA_RUNTIME,
    JAVA_RUNTIME_OVERRIDES,
    MC_DIR,
    OPTIFINE_URL,
    STONE_CLIENT_URLS,
)
from .download_manager import (
    DownloadJob,
    DownloadManager,
    ProgressReporter,
    adaptive_worker_count,
    get_session,
)

_MANAGER = DownloadManager()

OPTIFINE_FILENAME = "OptiFine_1.8.9_HD_U_M5.jar"
MANAGED_MODS_FILE = ".stone-managed.json"

CUSTOM_CLIENT_VERSIONS = {"1.20.1"}

# ---------------------------------------------------------------------------
# Small filesystem / HTTP helpers
# ---------------------------------------------------------------------------


def _read_json(path: str):
    try:
        with open(path, "r", encoding="utf-8") as file:
            return json.load(file)
    except (OSError, ValueError):
        return None


def _write_json(path: str, data) -> None:
    try:
        with open(path, "w", encoding="utf-8") as file:
            json.dump(data, file)
    except OSError:
        pass


def safe_get(url: str, retries: int = 3, timeout: int = 30, **kwargs):
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            response = get_session().get(url, timeout=timeout, **kwargs)
            response.raise_for_status()
            return response
        except requests.RequestException as error:
            last_error = error
            if attempt + 1 < retries:
                time.sleep(0.5 * (attempt + 1))
    raise last_error if last_error is not None else requests.RequestException(url)


# ---------------------------------------------------------------------------
# Mods
# ---------------------------------------------------------------------------


def ensure_mods_directory() -> str:
    mods_path = os.path.join(MC_DIR, "mods")
    if os.path.exists(mods_path) and not os.path.isdir(mods_path):
        os.remove(mods_path)
    os.makedirs(mods_path, exist_ok=True)
    return mods_path


def clear_mods() -> None:
    mods_dir = ensure_mods_directory()
    for fname in os.listdir(mods_dir):
        if fname == MANAGED_MODS_FILE:
            continue
        fpath = os.path.join(mods_dir, fname)
        try:
            if os.path.isfile(fpath):
                os.remove(fpath)
        except OSError:
            pass
    _write_json(os.path.join(mods_dir, MANAGED_MODS_FILE), [])


def _managed_mods_path() -> str:
    return os.path.join(ensure_mods_directory(), MANAGED_MODS_FILE)


def _read_managed_mods() -> set[str]:
    data = _read_json(_managed_mods_path())
    if isinstance(data, list):
        return {str(name) for name in data}
    return set()


def _write_managed_mods(names: set[str]) -> None:
    _write_json(_managed_mods_path(), sorted(names))


def prune_managed_mods(desired: set[str]) -> None:
    """Remove launcher-managed mods that the selected version no longer uses.

    Files the user added themselves are never touched, and files that are
    still needed are kept, so switching versions does not re-download every
    mod on the next launch.
    """
    mods_dir = ensure_mods_directory()
    for name in _read_managed_mods() - desired:
        path = os.path.join(mods_dir, os.path.basename(name))
        try:
            if os.path.isfile(path):
                os.remove(path)
        except OSError:
            pass
    _write_managed_mods(desired)


def _modrinth_versions(slug: str, game_version: str) -> list[dict]:
    response = safe_get(
        f"https://api.modrinth.com/v2/project/{slug}/version",
        params={
            "game_versions": f'["{game_version}"]',
            "loaders": '["fabric"]',
        },
        timeout=20,
    )
    data = response.json()
    return data if isinstance(data, list) else []


def _modrinth_version_by_id(version_id: str) -> dict | None:
    response = safe_get(
        f"https://api.modrinth.com/v2/version/{version_id}",
        timeout=20,
    )
    data = response.json()
    return data if isinstance(data, dict) else None


def _download_mod_version(version: dict) -> str | None:
    files = version.get("files") or []
    if not files:
        return None
    file_info = next((item for item in files if item.get("primary")), files[0])
    filename = file_info.get("filename")
    url = file_info.get("url")
    if not filename or not url:
        return None
    destination = os.path.join(ensure_mods_directory(), filename)
    hashes = file_info.get("hashes") or {}
    _MANAGER.fetch(
        url,
        destination,
        size=file_info.get("size"),
        sha1=hashes.get("sha1"),
    )
    return filename


def download_mod_modrinth(slug: str, game_version: str) -> str | None:
    """Download a Modrinth mod and return the installed file name."""
    versions = _modrinth_versions(slug, game_version)
    if not versions:
        return None
    return _download_mod_version(versions[0])


def _download_iris_with_dependencies(
    version: dict, game_version: str
) -> set[str]:
    """Download Iris plus the exact mod versions its metadata requires.

    Iris pins a specific Sodium build. That build can be tagged for a newer
    patch release than the base Minecraft version the launcher starts, so
    selecting Sodium independently would install an incompatible version.
    """
    installed: set[str] = set()
    iris_filename = _download_mod_version(version)
    if iris_filename:
        installed.add(iris_filename)

    resolved_dependency = False
    for dependency in version.get("dependencies", []):
        if dependency.get("dependency_type") != "required":
            continue
        dependency_id = dependency.get("version_id")
        if not dependency_id:
            continue
        dependency_version = _modrinth_version_by_id(str(dependency_id))
        if dependency_version is None:
            raise Exception(
                f"Could not resolve required mod dependency {dependency_id}"
            )
        filename = _download_mod_version(dependency_version)
        if filename:
            installed.add(filename)
            resolved_dependency = True

    # Older Iris metadata may not pin a version id; fall back to the newest
    # Sodium build for this game version so shaders still get their dependency.
    if not resolved_dependency:
        sodium_filename = download_mod_modrinth("sodium", game_version)
        if sodium_filename:
            installed.add(sodium_filename)
    return installed


def _prune_other_sodium_files(keep: set[str]) -> None:
    """Remove stale Sodium jars so only the selected one can load."""
    mods_dir = ensure_mods_directory()
    for name in os.listdir(mods_dir):
        if (
            name.lower().startswith("sodium-fabric-")
            and name.lower().endswith(".jar")
            and name not in keep
        ):
            try:
                os.remove(os.path.join(mods_dir, name))
            except OSError:
                pass


def download_optifine() -> str:
    destination = os.path.join(ensure_mods_directory(), OPTIFINE_FILENAME)
    _MANAGER.fetch(OPTIFINE_URL, destination)
    return OPTIFINE_FILENAME


def download_stone_client(version_id: str) -> str | None:
    url = STONE_CLIENT_URLS.get(version_id)
    if not url:
        return None
    filename = f"stone-client-{version_id}.jar"
    destination = os.path.join(ensure_mods_directory(), filename)
    _MANAGER.fetch(url, destination)
    return filename


def download_launcher_mods(
    version_id: str,
    shaders_enabled: bool,
    status_callback=None,
) -> None:
    """Download every mod the launcher manages, in parallel, once.

    Previously the mods folder was cleared before each launch and all mods
    were downloaded again.  Managed mods are now tracked in a manifest and
    only missing files are fetched.
    """
    tasks = [
        ("modrinth", "entityculling", "Entity Culling"),
        ("modrinth", "fabric-api", "Fabric API"),
    ]
    if not shaders_enabled:
        tasks.append(("modrinth", "sodium", "Sodium"))
    if version_id in CUSTOM_CLIENT_VERSIONS:
        tasks.append(("stone", version_id, "Stone Client"))
    if supports_worldhost(version_id):
        tasks.append(("modrinth", "world-host", "WorldHost"))
    elif status_callback:
        status_callback("Skipping WorldHost (unsupported version)")
    if shaders_enabled:
        tasks.append(("iris", version_id, "Iris Shaders"))

    desired: set[str] = set()
    desired_lock = threading.Lock()

    def run(kind: str, value: str, label: str) -> None:
        if status_callback:
            status_callback(f"Downloading {label}…")
        if kind == "stone":
            filename = download_stone_client(value)
            filenames = {filename} if filename else set()
        elif kind == "iris":
            versions = _modrinth_versions("iris", value)
            if not versions:
                raise Exception(f"No Iris build found for Minecraft {value}")
            filenames = _download_iris_with_dependencies(versions[0], value)
        else:
            filename = download_mod_modrinth(value, version_id)
            filenames = {filename} if filename else set()
        if filenames:
            with desired_lock:
                desired.update(filenames)

    errors: list[Exception] = []
    with ThreadPoolExecutor(
        max_workers=min(4, len(tasks)),
        thread_name_prefix="stone-mods",
    ) as pool:
        futures = [pool.submit(run, *task) for task in tasks]
        for future in futures:
            error = future.exception()
            if error is not None:
                errors.append(error)

    # Keep the previous, working mod set when a download fails instead of
    # pruning files the failed download was supposed to replace.
    if errors:
        raise errors[0]
    prune_managed_mods(desired)
    _prune_other_sodium_files(desired)


def supports_worldhost(version_id: str) -> bool:
    try:
        parts = version_id.split(".")
        major = int(parts[0])
        minor = int(parts[1])
        patch = int(parts[2]) if len(parts) > 2 else 0
        current = (major, minor, patch)
        return (1, 16, 5) <= current <= (1, 21, 5)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Java runtime
# ---------------------------------------------------------------------------

_java_version_cache: dict[str, tuple[float, int | None]] = {}
_java_version_lock = threading.Lock()


def java_requirements(version_id: str) -> tuple[int, str]:
    """Return the (major version, Mojang runtime name) for a game version."""
    override = JAVA_RUNTIME_OVERRIDES.get(version_id)
    if override:
        return int(override["major"]), str(override["runtime"])
    return JAVA_MAJOR, JAVA_RUNTIME


def java_runtime_name(version_id: str) -> str:
    return java_requirements(version_id)[1]


def java_major_version(java_executable: str) -> int | None:
    """Return the Java major version without spawning Java on every launch."""
    try:
        mtime = os.path.getmtime(java_executable)
    except OSError:
        return None

    with _java_version_lock:
        cached = _java_version_cache.get(java_executable)
        if cached is not None and cached[0] == mtime:
            return cached[1]

    major: int | None = None
    try:
        result = subprocess.run(
            [java_executable, "-version"],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        output = (result.stderr or "") + (result.stdout or "")
        match = re.search(r'version "(\d+)(?:\.(\d+))?', output)
        if match:
            major = int(match.group(1))
            if major == 1 and match.group(2):
                major = int(match.group(2))
    except (OSError, subprocess.SubprocessError):
        major = None

    with _java_version_lock:
        _java_version_cache[java_executable] = (mtime, major)
    return major


def _bundled_java_candidates(
    required_major: int, runtime_name: str = JAVA_RUNTIME
) -> list[str]:
    roots: list[str] = []
    if getattr(sys, "frozen", False):
        roots.append(os.path.dirname(os.path.abspath(sys.executable)))
    else:
        roots.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        roots.append(BASE_DIR)
    roots.append(APP_ROOT)

    executable = "java.exe" if os.name == "nt" else "java"
    candidates: list[str] = []
    for root in dict.fromkeys(roots):
        for folder in (
            f"jre{required_major}",
            "jre",
            "java",
            os.path.join("runtime", runtime_name),
        ):
            candidate = os.path.join(root, folder, "bin", executable)
            if os.path.isfile(candidate):
                candidates.append(candidate)
    return list(dict.fromkeys(candidates))


def _compatible_java(major: int | None, required_major: int) -> bool:
    if major is None:
        return False
    # Forge on 1.8.9 breaks on Java 9+, so legacy must stay on exactly 8.
    if required_major <= 8:
        return major == 8
    return major >= required_major


def _find_system_java(required_major: int) -> str | None:
    candidates: list[str] = []
    java_home = os.environ.get("JAVA_HOME")
    if java_home:
        candidates.append(
            os.path.join(java_home, "bin", "java.exe" if os.name == "nt" else "java")
        )
    on_path = shutil.which("java")
    if on_path:
        candidates.append(on_path)

    for candidate in candidates:
        if os.path.isfile(candidate):
            if _compatible_java(java_major_version(candidate), required_major):
                return candidate
    return None


def ensure_java(version_id: str, status_callback=None) -> str:
    """Return a usable Java executable, installing the pinned runtime once.

    Lookup order: a JRE shipped next to the launcher (jre8/jre/jre25), the
    already installed pinned Mojang runtime, a compatible system Java, then a
    single Mojang runtime install.  No per-Minecraft-version runtimes are
    downloaded.
    """
    required_major, runtime_name = java_requirements(version_id)

    for candidate in _bundled_java_candidates(required_major, runtime_name):
        if _compatible_java(java_major_version(candidate), required_major):
            return candidate

    installed = minecraft_launcher_lib.runtime.get_executable_path(runtime_name, MC_DIR)
    if installed and os.path.isfile(installed):
        major = java_major_version(installed)
        if major is None or _compatible_java(major, required_major):
            return installed

    system_java = _find_system_java(required_major)
    if system_java:
        return system_java

    if status_callback:
        status_callback(f"Installing Java {required_major}…")
    callback = {"setStatus": status_callback} if status_callback else {}
    minecraft_launcher_lib.runtime.install_jvm_runtime(
        runtime_name,
        MC_DIR,
        callback=callback,
        max_workers=adaptive_worker_count(),
    )
    java_executable = minecraft_launcher_lib.runtime.get_executable_path(runtime_name, MC_DIR)
    if not java_executable or not os.path.isfile(java_executable):
        raise Exception(f"Java {required_major} installation failed")
    return java_executable


def get_runtime_name(version_data: dict, version_id: str | None = None) -> str:
    return java_runtime_name(version_id or version_data.get("id", ""))


# ---------------------------------------------------------------------------
# Version / asset downloads
# ---------------------------------------------------------------------------


def _version_json_path(version_id: str) -> str:
    return os.path.join(MC_DIR, "versions", version_id, f"{version_id}.json")


def download_version_json(version_entry: dict) -> str:
    version_id = version_entry["id"]
    json_path = _version_json_path(version_id)
    if _read_json(json_path) is not None:
        return json_path

    try:
        os.remove(json_path)
    except OSError:
        pass
    _MANAGER.fetch(
        version_entry["url"],
        json_path,
        size=version_entry.get("size"),
        sha1=version_entry.get("sha1"),
    )
    return json_path


def get_version_data(version_entry: dict) -> dict:
    """Read version metadata from disk; only touch the network if missing."""
    json_path = download_version_json(version_entry)
    with open(json_path, "r", encoding="utf-8") as file:
        return json.load(file)


def setup_directories(version_id: str) -> None:
    for sub in ("versions/" + version_id, "libraries", "assets", f"natives/{version_id}"):
        os.makedirs(os.path.join(MC_DIR, sub), exist_ok=True)


def download_client(version_data: dict, version_id: str, progress_callback=None) -> str:
    client = version_data["downloads"]["client"]
    jar_path = os.path.join(MC_DIR, "versions", version_id, f"{version_id}.jar")
    reporter = ProgressReporter(progress_callback, 0, 10, 1)
    _MANAGER.fetch(
        client["url"],
        jar_path,
        size=client.get("size"),
        sha1=client.get("sha1"),
    )
    reporter.finish()
    return jar_path


def _library_jobs(version_data: dict) -> list[DownloadJob]:
    jobs: list[DownloadJob] = []
    for library in version_data.get("libraries", []):
        artifact = library.get("downloads", {}).get("artifact")
        if not artifact:
            continue
        url = artifact.get("url")
        relative_path = artifact.get("path")
        if not url or not relative_path:
            continue
        jobs.append(
            DownloadJob(
                url,
                os.path.join(MC_DIR, "libraries", relative_path),
                artifact.get("size"),
                artifact.get("sha1"),
            )
        )
    return jobs


def download_libraries(version_data: dict, progress_callback=None, max_workers=None) -> None:
    jobs = _library_jobs(version_data)
    reporter = ProgressReporter(progress_callback, 10, 40, len(jobs))
    _MANAGER.fetch_many(jobs, max_workers=max_workers, progress=reporter)
    reporter.finish()


def _native_key() -> str | None:
    system = platform.system().lower()
    return {
        "windows": "natives-windows",
        "linux": "natives-linux",
        "darwin": "natives-macos",
    }.get(system)


def download_natives(
    version_data: dict,
    version_id: str,
    progress_callback=None,
    max_workers=None,
) -> None:
    native_key = _native_key()
    if not native_key:
        if progress_callback:
            progress_callback(55)
        return

    natives_dir = os.path.join(MC_DIR, "natives", version_id)
    jobs: list[DownloadJob] = []
    for library in version_data.get("libraries", []):
        native = library.get("downloads", {}).get("classifiers", {}).get(native_key)
        if not native:
            continue
        url = native.get("url")
        relative_path = native.get("path")
        if not url or not relative_path:
            continue
        jobs.append(
            DownloadJob(
                url,
                os.path.join(MC_DIR, "libraries", relative_path),
                native.get("size"),
                native.get("sha1"),
            )
        )

    reporter = ProgressReporter(progress_callback, 40, 55, len(jobs))
    _MANAGER.fetch_many(jobs, max_workers=max_workers, progress=reporter)

    state_path = os.path.join(natives_dir, ".stone-natives.json")
    expected = {
        "archives": [
            [os.path.basename(job.path), job.size or 0] for job in jobs
        ]
    }
    if _read_json(state_path) == expected:
        reporter.finish()
        return

    if os.path.isdir(natives_dir):
        shutil.rmtree(natives_dir, ignore_errors=True)
    os.makedirs(natives_dir, exist_ok=True)
    for job in jobs:
        try:
            with zipfile.ZipFile(job.path) as archive:
                archive.extractall(natives_dir)
        except (OSError, zipfile.BadZipFile):
            pass
    _write_json(state_path, expected)
    reporter.finish()


def download_assets(version_data: dict, progress_callback=None, max_workers=None) -> None:
    index_info = version_data.get("assetIndex")
    if not index_info:
        if progress_callback:
            progress_callback(95)
        return

    asset_id = version_data.get("assets") or index_info.get("id") or "legacy"
    index_path = os.path.join(MC_DIR, "assets", "indexes", f"{asset_id}.json")
    index_data = _read_json(index_path)
    if index_data is None:
        try:
            os.remove(index_path)
        except OSError:
            pass
        _MANAGER.fetch(
            index_info["url"],
            index_path,
            size=index_info.get("size"),
            sha1=index_info.get("sha1"),
        )
        index_data = _read_json(index_path)
    if not index_data:
        raise Exception("Could not read the Minecraft asset index")

    # Asset names map to hashes; several names may share one object.
    objects: dict[str, int | None] = {}
    for entry in index_data.get("objects", {}).values():
        file_hash = entry.get("hash")
        if file_hash:
            objects[file_hash] = entry.get("size")

    jobs = [
        DownloadJob(
            f"https://resources.download.minecraft.net/{file_hash[:2]}/{file_hash}",
            os.path.join(MC_DIR, "assets", "objects", file_hash[:2], file_hash),
            size,
            file_hash,
        )
        for file_hash, size in objects.items()
    ]
    reporter = ProgressReporter(progress_callback, 55, 95, len(jobs))
    _MANAGER.fetch_many(jobs, max_workers=max_workers, progress=reporter)
    reporter.finish()


# ---------------------------------------------------------------------------
# Fabric / Forge install
# ---------------------------------------------------------------------------

FABRIC_LOADER_CACHE = os.path.join(DATA_DIR, "fabric_loader.json")
FABRIC_LOADER_CACHE_TTL = 6 * 60 * 60


def _latest_fabric_loader() -> str:
    import minecraft_launcher_lib.fabric as _fabric

    cached = _read_json(FABRIC_LOADER_CACHE)
    if (
        isinstance(cached, dict)
        and cached.get("version")
        and time.time() - float(cached.get("time", 0)) < FABRIC_LOADER_CACHE_TTL
    ):
        return str(cached["version"])

    loader_version = _fabric.get_latest_loader_version()
    _write_json(
        FABRIC_LOADER_CACHE,
        {"version": loader_version, "time": time.time()},
    )
    return loader_version


def _maven_library_path(name: str) -> str:
    """Return the ``libraries/`` path for a Maven coordinate."""
    if "@" in name:
        name, suffix = name.split("@", 1)
    else:
        suffix = "jar"
    parts = name.split(":")
    if len(parts) < 3:
        return ""
    group, artifact, version = parts[:3]
    filename = (
        f"{artifact}-{version}"
        + "".join(f"-{part}" for part in parts[3:])
        + f".{suffix}"
    )
    return os.path.join(
        MC_DIR, "libraries", *group.split("."), artifact, version, filename
    )


def _fabric_install_complete(
    version_id: str, fabric_id: str, json_path: str
) -> bool:
    data = _read_json(json_path)
    if not isinstance(data, dict):
        return False

    for library in data.get("libraries", []):
        name = library.get("name") if isinstance(library, dict) else None
        if not name:
            continue
        path = _maven_library_path(str(name))
        if not path or not os.path.isfile(path):
            return False

    # Fabric profiles inherit the vanilla jar.  MLL builds the classpath from
    # versions/<fabric id>/<fabric id>.jar when the profile has no "jar" key.
    fabric_dir = os.path.join(MC_DIR, "versions", fabric_id)
    fabric_jar = os.path.join(fabric_dir, f"{fabric_id}.jar")
    if os.path.isfile(fabric_jar):
        return True
    vanilla_jar = os.path.join(MC_DIR, "versions", version_id, f"{version_id}.jar")
    if not os.path.isfile(vanilla_jar):
        return False
    try:
        os.makedirs(fabric_dir, exist_ok=True)
        shutil.copyfile(vanilla_jar, fabric_jar)
    except OSError:
        return False
    return True


def _run_fabric_installer(
    version_id: str,
    loader_version: str,
    java_exec: str,
    status_callback=None,
) -> None:
    metadata = safe_get(
        "https://maven.fabricmc.net/net/fabricmc/fabric-installer/maven-metadata.xml",
        timeout=30,
    )
    match = re.search(r"<latest>([^<]+)</latest>", metadata.text)
    if not match:
        raise Exception("Could not determine the Fabric installer version")
    installer_version = match.group(1)
    installer_url = (
        "https://maven.fabricmc.net/net/fabricmc/fabric-installer/"
        f"{installer_version}/fabric-installer-{installer_version}.jar"
    )
    if status_callback:
        status_callback(f"Installing Fabric {loader_version}…")

    with tempfile.TemporaryDirectory(prefix="stone-fabric-") as temp_dir:
        installer_path = os.path.join(temp_dir, "fabric-installer.jar")
        checksum = ""
        try:
            response = safe_get(installer_url + ".sha1", timeout=15)
            candidate = response.text.strip().split()[0].lower()
            if re.fullmatch(r"[0-9a-f]{40}", candidate):
                checksum = candidate
        except Exception:
            checksum = ""

        _MANAGER.fetch(installer_url, installer_path, sha1=checksum or None)
        command = [
            java_exec,
            "-jar",
            installer_path,
            "client",
            "-dir",
            MC_DIR,
            "-mcversion",
            version_id,
            "-loader",
            loader_version,
            "-noprofile",
            "-snapshot",
        ]
        result = subprocess.run(
            command,
            cwd=temp_dir,
            capture_output=True,
            text=True,
            timeout=180,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if result.returncode != 0:
            output = (result.stderr or result.stdout or "").strip()
            detail = f": {output[-800:]}" if output else ""
            raise Exception(
                f"Fabric installer exited with code {result.returncode}{detail}"
            )


def install_fabric(
    version_id: str, java_exec: str, status_callback=None
) -> str:
    loader_version = _latest_fabric_loader()
    fabric_id = f"fabric-loader-{loader_version}-{version_id}"
    json_path = os.path.join(MC_DIR, "versions", fabric_id, f"{fabric_id}.json")

    if _fabric_install_complete(version_id, fabric_id, json_path):
        return fabric_id

    last_error: Exception | None = None
    for attempt in range(3):
        try:
            _run_fabric_installer(
                version_id,
                loader_version,
                java_exec,
                status_callback=status_callback,
            )
        except Exception as error:
            last_error = error
        if _fabric_install_complete(version_id, fabric_id, json_path):
            return fabric_id
        if attempt < 2:
            time.sleep(1)

    raise Exception(f"Fabric install failed: {last_error or 'profile was not created'}")


def install_forge(
    version_id: str,
    forge_version_string: str,
    java_exec: str,
    status_callback=None,
) -> str:
    if status_callback:
        status_callback("Installing Forge…")

    mc_forge.install_forge_version(
        forge_version_string,
        MC_DIR,
        callback={"setStatus": status_callback} if status_callback else {},
        java=java_exec,
    )

    versions_dir = os.path.join(MC_DIR, "versions")
    forge_id = next(
        (d for d in os.listdir(versions_dir) if "1.8.9" in d and "forge" in d.lower()),
        None,
    )
    if not forge_id:
        raise Exception("Forge install completed but version folder not found")

    forge_ver_dir = os.path.join(versions_dir, forge_id)
    json_path = os.path.join(forge_ver_dir, f"{forge_id}.json")
    jar_path = os.path.join(forge_ver_dir, f"{forge_id}.jar")

    if not os.path.exists(jar_path):
        if status_callback:
            status_callback("Copying vanilla jar for Forge…")
        vanilla_jar = os.path.join(versions_dir, version_id, f"{version_id}.jar")
        if os.path.exists(vanilla_jar):
            shutil.copy2(vanilla_jar, jar_path)
        else:
            raise Exception(f"Vanilla jar missing at {vanilla_jar}")

    if not os.path.exists(json_path):
        for root, _, files in os.walk(versions_dir):
            for fname in files:
                if "forge" in fname.lower() and fname.endswith(".json"):
                    os.makedirs(forge_ver_dir, exist_ok=True)
                    shutil.copy2(os.path.join(root, fname), json_path)
                    break

    if not os.path.exists(json_path):
        raise Exception(f"Forge JSON missing at {json_path}")

    return forge_id
