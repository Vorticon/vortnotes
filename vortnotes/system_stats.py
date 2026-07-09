"""Lightweight runtime/system stats for the Settings page."""

from __future__ import annotations

import os
import time
from pathlib import Path

CGROUP_ROOT = Path("/sys/fs/cgroup")


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _read_int(path: Path) -> int | None:
    raw = _read_text(path)
    if not raw or raw == "max":
        return None
    try:
        return int(raw)
    except Exception:
        return None


def _fmt_bytes(num_bytes: int | None) -> str:
    if num_bytes is None:
        return "Unlimited"
    size = float(max(0, num_bytes))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.2f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.2f} TB"


def _cpu_usage_usec(cgroup_root: Path = CGROUP_ROOT) -> int | None:
    for line in _read_text(cgroup_root / "cpu.stat").splitlines():
        key, _, value = line.partition(" ")
        if key == "usage_usec":
            try:
                return int(value)
            except Exception:
                return None
    return None


def _cpu_limit_cores(cgroup_root: Path = CGROUP_ROOT) -> float:
    raw = _read_text(cgroup_root / "cpu.max")
    quota_raw, _, period_raw = raw.partition(" ")
    try:
        if quota_raw and quota_raw != "max":
            quota = int(quota_raw)
            period = int(period_raw or "100000")
            if quota > 0 and period > 0:
                return max(quota / period, 0.01)
    except Exception:
        pass
    return float(os.cpu_count() or 1)


def _sample_cpu_percent(cgroup_root: Path = CGROUP_ROOT, sample_seconds: float = 0.12) -> float | None:
    first = _cpu_usage_usec(cgroup_root)
    if first is None:
        return None
    start = time.monotonic()
    time.sleep(max(0.02, sample_seconds))
    second = _cpu_usage_usec(cgroup_root)
    elapsed = time.monotonic() - start
    if second is None or elapsed <= 0:
        return None
    used_seconds = max(0, second - first) / 1_000_000
    percent = (used_seconds / elapsed) / _cpu_limit_cores(cgroup_root) * 100
    return round(max(0.0, min(percent, 999.9)), 1)


def container_system_stats(cgroup_root: Path = CGROUP_ROOT) -> dict:
    memory_current = _read_int(cgroup_root / "memory.current")
    memory_limit = _read_int(cgroup_root / "memory.max")
    memory_pct = None
    if memory_current is not None and memory_limit:
        memory_pct = round((memory_current / memory_limit) * 100, 1)

    pids_current = _read_int(cgroup_root / "pids.current")
    pids_max_raw = _read_text(cgroup_root / "pids.max") or "Unavailable"
    cpu_percent = _sample_cpu_percent(cgroup_root)
    cpu_limit = _cpu_limit_cores(cgroup_root)

    return {
        "available": any((cgroup_root / name).exists() for name in ("cpu.stat", "memory.current", "pids.current")),
        "hostname": os.getenv("HOSTNAME") or _read_text(Path("/etc/hostname")) or "Unavailable",
        "source": "container cgroups",
        "cpu_percent": cpu_percent,
        "cpu_percent_display": f"{cpu_percent:.1f}%" if cpu_percent is not None else "Unavailable",
        "cpu_limit_cores": round(cpu_limit, 2),
        "cpu_limit_display": f"{cpu_limit:.2f} cores",
        "memory_current": memory_current,
        "memory_current_display": _fmt_bytes(memory_current) if memory_current is not None else "Unavailable",
        "memory_limit": memory_limit,
        "memory_limit_display": _fmt_bytes(memory_limit),
        "memory_percent": memory_pct,
        "memory_percent_display": f"{memory_pct:.1f}%" if memory_pct is not None else "Unavailable",
        "pids_current": pids_current,
        "pids_current_display": str(pids_current) if pids_current is not None else "Unavailable",
        "pids_max_display": pids_max_raw,
    }
