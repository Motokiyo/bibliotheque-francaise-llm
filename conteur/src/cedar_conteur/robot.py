"""Reachy Mini controller for storyteller mode.

Three-tier connection strategy:
1. Try to attach to an existing daemon at localhost:8000 (Pollen desktop app running)
2. If absent, spawn our own daemon via Pollen's bundled Python venv (using
   the `mockup-sim` mode that does not require the Tauri front-end)
3. If both fail, fall back to a console-only mock

Antennas in annotations are DEGREES (-30..+30), converted to radians at send time.
"""

import logging
import math
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DESKTOP_APP_SUPPORT = Path.home() / "Library" / "Application Support" / "com.pollen-robotics.reachy-mini"
DAEMON_PORT = 8000
DAEMON_HEALTH_URL = f"http://127.0.0.1:{DAEMON_PORT}/api/daemon/status"
SPAWN_TIMEOUT_S = 20

try:
    from reachy_mini import ReachyMini
    HAS_SDK = True
except Exception as _exc:  # noqa: BLE001
    HAS_SDK = False
    ReachyMini = None
    logger.info("reachy_mini SDK unavailable (%s) — robot will run in mock mode", _exc)


def _daemon_already_running() -> bool:
    try:
        r = httpx.get(DAEMON_HEALTH_URL, timeout=1.0)
        return r.status_code == 200
    except Exception:
        return False


def _wait_for_daemon(timeout: float = SPAWN_TIMEOUT_S) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _daemon_already_running():
            return True
        time.sleep(0.5)
    return False


def _spawn_pollen_daemon() -> subprocess.Popen | None:
    """Spawn the Pollen daemon (Python 3.12) in mockup-sim mode.

    Uses `/bin/sh -c "env -i ... python3 ..."` to fully isolate the subprocess
    from libraries already loaded in our Python 3.13 parent (otherwise SIGSEGV
    on conflicting native dyld between pyrubberband/gstreamer_python and the
    Pollen GStreamer bundle).
    """
    python_bin = DESKTOP_APP_SUPPORT / ".venv" / "bin" / "python3"
    if not python_bin.exists():
        logger.warning("Pollen desktop venv not found at %s — cannot spawn daemon", python_bin)
        return None

    desktop = str(DESKTOP_APP_SUPPORT)
    shell_cmd = (
        f'cd "{desktop}" && '
        f'exec env -i HOME="$HOME" USER="$USER" '
        f'PATH="{desktop}/.venv/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin" '
        f'"{desktop}/.venv/bin/python3" -m reachy_mini.daemon.app.main '
        f'--no-wake-up-on-start --mockup-sim'
    )

    home = os.environ.get("HOME", "/Users/alexandre")
    user = os.environ.get("USER", "alexandre")

    log_path = Path("/tmp/cedar-conteur-pollen-daemon.log")
    logger.info("Spawning Pollen daemon via /bin/sh + env -i (log: %s)", log_path)
    proc = subprocess.Popen(
        ["/bin/sh", "-c", shell_cmd],
        stdin=subprocess.DEVNULL,
        stdout=open(log_path, "w"),
        stderr=subprocess.STDOUT,
        start_new_session=True,
        env={"HOME": home, "USER": user},
    )
    return proc


class RobotController:
    def __init__(self, use_sim: bool = True):
        self.mini = None
        self._connected = False
        self._lock = threading.Lock()
        self._mode = "mock"
        self._spawned_proc: subprocess.Popen | None = None
        if not HAS_SDK:
            return

        # Tier 1: attach to existing daemon
        if _daemon_already_running():
            if self._try_attach(use_sim):
                self._mode = "sim-attached"
                return

        # Tier 2: spawn our own daemon
        self._spawned_proc = _spawn_pollen_daemon()
        if self._spawned_proc and _wait_for_daemon(SPAWN_TIMEOUT_S):
            if self._try_attach(use_sim):
                self._mode = "sim-spawned"
                return
            logger.warning("Daemon spawned but ReachyMini attach failed")
        else:
            logger.warning("Daemon spawn or health check failed")

        # Tier 3: fall back to mock
        if self._spawned_proc and self._spawned_proc.poll() is None:
            self._spawned_proc.terminate()
            self._spawned_proc = None
        self._connected = False
        self._mode = "mock"

    def _try_attach(self, use_sim: bool) -> bool:
        try:
            self.mini = ReachyMini(use_sim=use_sim, media_backend="no_media", spawn_daemon=False)
            self._connected = True
            logger.info("ReachyMini attached on localhost:%d", DAEMON_PORT)
            return True
        except Exception as exc:
            logger.warning("ReachyMini attach failed: %s", exc)
            self.mini = None
            return False

    @property
    def mode(self) -> str:
        return self._mode

    def set_perso_antennas(self, perso_name: str | None, annotations: dict[str, Any]) -> tuple[float, float]:
        left_deg, right_deg = self._lookup(perso_name, annotations)
        left_rad = math.radians(left_deg)
        right_rad = math.radians(right_deg)
        if not self._connected or self.mini is None:
            logger.info("[MOCK robot] antennas perso=%s L=%.1f° R=%.1f°", perso_name, left_deg, right_deg)
            return (left_deg, right_deg)
        try:
            with self._lock:
                self.mini.set_target(antennas=[left_rad, right_rad])
        except Exception as exc:
            logger.warning("set_perso_antennas failed: %s", exc)
        return (left_deg, right_deg)

    def reset_pose(self) -> None:
        if not self._connected or self.mini is None:
            return
        try:
            with self._lock:
                self.mini.set_target(antennas=[0.0, 0.0])
        except Exception as exc:
            logger.warning("reset_pose failed: %s", exc)

    def shutdown(self) -> None:
        if self._connected and self.mini is not None:
            try:
                self.mini.__exit__(None, None, None)
            except Exception:
                pass
        self._connected = False
        # Kill our spawned daemon if we own it
        if self._spawned_proc is not None and self._spawned_proc.poll() is None:
            logger.info("Stopping spawned Pollen daemon")
            self._spawned_proc.terminate()
            try:
                self._spawned_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._spawned_proc.kill()
        self._spawned_proc = None

    def _lookup(self, perso_name: str | None, annotations: dict[str, Any]) -> tuple[float, float]:
        import unicodedata

        def _strip(s: str) -> str:
            return "".join(c for c in unicodedata.normalize("NFD", s)
                           if unicodedata.category(c) != "Mn")

        if not perso_name:
            return (0.0, 0.0)
        target = _strip(perso_name).strip().upper()
        for p in annotations.get("personnages", []) or []:
            if _strip(p.get("nom") or "").strip().upper() == target:
                return (float(p.get("antenna_left", 0) or 0), float(p.get("antenna_right", 0) or 0))
        return (0.0, 0.0)
