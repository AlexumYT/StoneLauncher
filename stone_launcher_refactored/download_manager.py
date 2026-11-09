"""Shared download engine for Stone Launcher.

Every Mojang / Modrinth / side-loaded file goes through this module instead of
ad-hoc ``requests.get`` calls.  Compared to the previous implementation it:

* uses one keep-alive ``requests.Session`` per worker thread (a single shared
  session serialised connections and made speed depend on the machine),
* keeps parallelism bounded (8-32 workers) so low-end PCs do not thrash,
* validates cached files by size, and verifies checksums for fresh downloads,
* retries transient failures with exponential backoff,
* downloads large files over a few ranged connections,
* throttles progress so the Qt event loop receives only a handful of signals
  per second instead of one signal per file.
"""

from __future__ import annotations

import hashlib
import os
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, Iterable

import requests
from requests.adapters import HTTPAdapter

from .config import APP_VERSION

USER_AGENT = f"StoneLauncher/{APP_VERSION}"
CONNECT_TIMEOUT = 15
READ_TIMEOUT = 60
CHUNK_SIZE = 1024 * 1024
MAX_WORKERS = 32
RANGE_THRESHOLD = 16 * 1024 * 1024
RANGE_PARTS = 4
MIN_RANGE_PART = 4 * 1024 * 1024
DEFAULT_RETRIES = 4

_thread_local = threading.local()


class DownloadError(Exception):
    """Raised when a file could not be downloaded after all retries."""


def get_session() -> requests.Session:
    """Return a keep-alive session belonging to the calling thread."""
    session = getattr(_thread_local, "session", None)
    if session is None:
        session = requests.Session()
        adapter = HTTPAdapter(pool_connections=4, pool_maxsize=8, max_retries=0)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        session.headers["User-Agent"] = USER_AGENT
        _thread_local.session = session
    return session


def adaptive_worker_count(preferred: int | None = None) -> int:
    """Return a deterministic, machine-sized worker count."""
    if preferred and preferred > 0:
        return min(preferred, MAX_WORKERS)
    cpu_count = os.cpu_count() or 4
    return max(8, min(MAX_WORKERS, cpu_count * 3))


def sha1_file(path: str) -> str:
    digest = hashlib.sha1()
    with open(path, "rb") as file:
        for block in iter(lambda: file.read(CHUNK_SIZE), b""):
            digest.update(block)
    return digest.hexdigest()


def ensure_directory(path: str) -> None:
    if path:
        os.makedirs(path, exist_ok=True)


def is_complete(
    path: str,
    size: int | None = None,
    sha1: str | None = None,
    check_hash: bool = False,
) -> bool:
    """Check whether ``path`` is already a usable copy of the remote file.

    Size is used for the common case because it is fast even for a few
    thousand assets.  Hashes are only calculated when explicitly requested
    or when no size information is available for a new download.
    """
    try:
        stat = os.stat(path)
    except OSError:
        return False
    if stat.st_size == 0 and (size is None or size > 0):
        return False
    if size is not None:
        return stat.st_size == size
    if check_hash and sha1:
        return sha1_file(path) == sha1
    return True


def _quiet_remove(path: str) -> None:
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def _download_stream(url: str, destination: str) -> None:
    with get_session().get(
        url,
        stream=True,
        headers={"Accept-Encoding": "identity"},
        timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
    ) as response:
        response.raise_for_status()
        length = response.headers.get("Content-Length")
        expected = int(length) if length and length.isdigit() else None
        with open(destination, "wb") as file:
            for block in response.iter_content(CHUNK_SIZE):
                if block:
                    file.write(block)
        actual = os.path.getsize(destination)
        if expected is not None and actual != expected:
            raise IOError(f"incomplete download ({actual}/{expected} bytes)")


def _range_supported(url: str) -> bool:
    try:
        with get_session().get(
            url,
            headers={"Range": "bytes=0-0", "Accept-Encoding": "identity"},
            stream=True,
            timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
        ) as response:
            return response.status_code == 206
    except requests.RequestException:
        return False


def _download_ranged(url: str, destination: str, size: int) -> bool:
    """Download ``size`` bytes with a few parallel range requests.

    Returns ``False`` when the server does not support ranges so the caller
    can fall back to a normal streaming download.
    """
    parts = max(1, min(RANGE_PARTS, size // MIN_RANGE_PART))
    if parts < 2 or not _range_supported(url):
        return False

    chunk_size = (size + parts - 1) // parts
    part_paths = [f"{destination}.{index}" for index in range(parts)]

    def fetch_part(index: int) -> None:
        start = index * chunk_size
        end = min(start + chunk_size, size) - 1
        part_path = part_paths[index]
        with get_session().get(
            url,
            headers={
                "Range": f"bytes={start}-{end}",
                "Accept-Encoding": "identity",
            },
            stream=True,
            timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
        ) as response:
            if response.status_code != 206:
                raise IOError(f"range request returned {response.status_code}")
            with open(part_path, "wb") as file:
                for block in response.iter_content(CHUNK_SIZE):
                    if block:
                        file.write(block)
        if os.path.getsize(part_path) != end - start + 1:
            raise IOError("short range download")

    try:
        with ThreadPoolExecutor(max_workers=parts, thread_name_prefix="stone-range") as pool:
            futures = [pool.submit(fetch_part, index) for index in range(parts)]
            for future in futures:
                future.result()
        with open(destination, "wb") as output:
            for part_path in part_paths:
                with open(part_path, "rb") as part:
                    while block := part.read(CHUNK_SIZE):
                        output.write(block)
        return os.path.getsize(destination) == size
    finally:
        for part_path in part_paths:
            _quiet_remove(part_path)


class ProgressReporter:
    """Thread-safe progress mapped onto a percentage range.

    Updates are emitted only when the integer percentage changes and at most
    every ``min_interval`` seconds, which keeps the GUI responsive even while
    thousands of small assets are downloading.
    """

    def __init__(
        self,
        callback: Callable[[int], None] | None,
        minimum: int = 0,
        maximum: int = 100,
        total: int = 0,
        min_interval: float = 0.06,
    ) -> None:
        self.callback = callback
        self.minimum = minimum
        self.maximum = maximum
        self.total = max(int(total), 0)
        self.min_interval = min_interval
        self._completed = 0
        self._value = minimum
        self._last_emit = 0.0
        self._lock = threading.Lock()

    def _value_for(self, completed: int) -> int:
        if self.total <= 0:
            return self.maximum
        span = self.maximum - self.minimum
        return self.minimum + int(span * min(completed, self.total) / self.total)

    def advance(self, amount: int = 1) -> None:
        # Emit while holding the lock so the GUI never receives an older
        # percentage after a newer one from another worker thread.
        with self._lock:
            self._completed += amount
            value = self._value_for(self._completed)
            if value <= self._value:
                return
            now = time.monotonic()
            if value < self.maximum and now - self._last_emit < self.min_interval:
                return
            self._value = value
            self._last_emit = now
            if self.callback is not None:
                self.callback(value)

    def finish(self) -> None:
        with self._lock:
            if self._value == self.maximum and self._completed >= self.total:
                return
            self._completed = self.total
            self._value = self.maximum
            if self.callback is not None:
                self.callback(self.maximum)


@dataclass(frozen=True)
class DownloadJob:
    url: str
    path: str
    size: int | None = None
    sha1: str | None = None


class DownloadManager:
    def __init__(self, retries: int = DEFAULT_RETRIES) -> None:
        self.retries = retries

    def fetch(
        self,
        url: str,
        path: str,
        size: int | None = None,
        sha1: str | None = None,
        *,
        retries: int | None = None,
        check_hash: bool = False,
    ) -> None:
        """Ensure ``path`` contains the file at ``url``.

        ``DownloadError`` is raised when all attempts fail so callers no
        longer have to silence or ignore a missing library.
        """
        if is_complete(path, size, sha1, check_hash=check_hash):
            return
        ensure_directory(os.path.dirname(path))

        attempts = retries if retries is not None else self.retries
        temp_path = path + ".part"
        last_error: Exception | None = None

        for attempt in range(1, attempts + 1):
            try:
                _quiet_remove(temp_path)
                downloaded = False
                if size is not None and size >= RANGE_THRESHOLD:
                    downloaded = _download_ranged(url, temp_path, size)
                if not downloaded:
                    _download_stream(url, temp_path)

                if size is not None:
                    actual = os.path.getsize(temp_path)
                    if actual != size:
                        raise IOError(f"size mismatch ({actual}/{size} bytes)")
                if sha1 and sha1_file(temp_path) != sha1:
                    raise IOError("checksum mismatch")

                os.replace(temp_path, path)
                return
            except Exception as error:  # noqa: BLE001 - retried below
                last_error = error
                _quiet_remove(temp_path)
                if attempt < attempts:
                    delay = min(0.5 * (2 ** (attempt - 1)), 5.0)
                    time.sleep(delay + random.uniform(0, 0.2))

        raise DownloadError(
            f"Could not download {os.path.basename(path)}: {last_error}"
        )

    def fetch_job(self, job: DownloadJob, progress: ProgressReporter | None = None) -> None:
        try:
            self.fetch(job.url, job.path, job.size, job.sha1)
        finally:
            if progress is not None:
                progress.advance()

    def fetch_many(
        self,
        jobs: Iterable[DownloadJob],
        max_workers: int | None = None,
        progress: ProgressReporter | None = None,
    ) -> None:
        """Download independent files using one shared worker pool."""
        pending: list[DownloadJob] = []
        for job in jobs:
            if is_complete(job.path, job.size, job.sha1):
                if progress is not None:
                    progress.advance()
            else:
                pending.append(job)

        if not pending:
            if progress is not None:
                progress.finish()
            return

        workers = min(adaptive_worker_count(max_workers), len(pending))
        first_error: Exception | None = None
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="stone-dl") as pool:
            futures = [pool.submit(self.fetch_job, job, progress) for job in pending]
            # Wait for every file so one failure does not leave a half-synced
            # installation, then report the first error.
            for future in futures:
                error = future.exception()
                if error is not None and first_error is None:
                    first_error = error

        if progress is not None:
            progress.finish()
        if first_error is not None:
            raise first_error
