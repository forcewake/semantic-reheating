"""Bounded test-only readers for the checked-in benchmark corpus."""

from __future__ import annotations

import os
import re
import stat
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import NoReturn

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CORPUS_ROOT = PROJECT_ROOT / "benchmark" / "corpus"
MAX_TRACE_BYTES = 1_048_576
MAX_CORPUS_BYTES = 33_554_432
MAX_LINE_BYTES = 262_144
MAX_EVENTS = 10_000
MAX_SMALL_PUBLIC_BYTES = 1_048_576
_TRACE_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\.jsonl")
_READ_CHUNK_BYTES = 65_536


@dataclass(frozen=True)
class CorpusLimits:
    """Explicit resource limits for test-data ingestion."""

    max_trace_bytes: int = MAX_TRACE_BYTES
    max_corpus_bytes: int = MAX_CORPUS_BYTES
    max_line_bytes: int = MAX_LINE_BYTES
    max_events: int = MAX_EVENTS

    def __post_init__(self) -> None:
        for value in (
            self.max_trace_bytes,
            self.max_corpus_bytes,
            self.max_line_bytes,
            self.max_events,
        ):
            if type(value) is not int or value <= 0:
                raise ValueError("corpus limits must be positive built-in integers")


DEFAULT_CORPUS_LIMITS = CorpusLimits()


@dataclass
class CorpusBudget:
    """Mutable exact built-in-integer aggregate used across one corpus read."""

    total_bytes: int = 0

    def __post_init__(self) -> None:
        if type(self.total_bytes) is not int or self.total_bytes < 0:
            raise ValueError("corpus budget must be a non-negative built-in integer")


@dataclass(frozen=True)
class CorpusTrace:
    """One safely bounded trace, retaining immutable newline-terminated bytes."""

    trace_path: str
    lines: tuple[bytes, ...]
    total_bytes: int


@dataclass(frozen=True)
class CorpusRead:
    """Safely bounded aggregate returned only after every requested trace passes."""

    traces: tuple[CorpusTrace, ...]
    total_bytes: int


def _unsafe() -> NoReturn:
    raise AssertionError("unsafe corpus input")


def _same_identity(left: os.stat_result, right: os.stat_result) -> bool:
    return left.st_dev == right.st_dev and left.st_ino == right.st_ino


def _trace_name(trace_path: object) -> str:
    if type(trace_path) is not str:
        _unsafe()
    if "\\" in trace_path or "\r" in trace_path or "\n" in trace_path:
        _unsafe()
    relative = PurePosixPath(trace_path)
    if relative.is_absolute() or ".." in relative.parts:
        _unsafe()
    name = relative.name
    if (
        relative.parts != ("benchmark", "corpus", name)
        or trace_path != f"benchmark/corpus/{name}"
        or _TRACE_NAME.fullmatch(name) is None
    ):
        _unsafe()
    return name


def _validated_root(root: Path) -> Path:
    try:
        metadata = root.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            _unsafe()
        return root.resolve(strict=True)
    except (OSError, ValueError):
        _unsafe()


def _open_readonly(path: Path) -> int:
    flags = os.O_RDONLY
    flags |= getattr(os, "O_NONBLOCK", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        return os.open(path, flags)
    except (OSError, ValueError):
        _unsafe()


def read_small_public_file(
    path: Path, *, max_bytes: int = MAX_SMALL_PUBLIC_BYTES
) -> bytes:
    """Read a small trusted JSON asset only after an explicit byte-size cap."""
    if type(max_bytes) is not int or max_bytes <= 0:
        raise ValueError("small-file limit must be a positive built-in integer")
    try:
        before = path.lstat()
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            _unsafe()
        if before.st_size > max_bytes:
            _unsafe()
    except (OSError, ValueError):
        _unsafe()
    fd = _open_readonly(path)
    try:
        during = os.fstat(fd)
        if not stat.S_ISREG(during.st_mode) or not _same_identity(before, during):
            _unsafe()
        raw = os.read(fd, max_bytes + 1)
        if len(raw) > max_bytes:
            _unsafe()
        after = os.fstat(fd)
        if (
            not stat.S_ISREG(after.st_mode)
            or not _same_identity(before, after)
            or after.st_size != before.st_size
            or after.st_mtime_ns != before.st_mtime_ns
            or len(raw) != before.st_size
        ):
            _unsafe()
        return raw
    except (OSError, ValueError):
        _unsafe()
    finally:
        os.close(fd)


def _read_trace(
    path: Path,
    trace_path: str,
    *,
    limits: CorpusLimits,
    remaining_bytes: int,
    before_open: Callable[[Path], None] | None,
) -> CorpusTrace:
    try:
        before = path.lstat()
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            _unsafe()
        if before.st_size > limits.max_trace_bytes or before.st_size > remaining_bytes:
            _unsafe()
    except (OSError, ValueError):
        _unsafe()

    if before_open is not None:
        before_open(path)
    fd = _open_readonly(path)
    try:
        during = os.fstat(fd)
        if not stat.S_ISREG(during.st_mode) or not _same_identity(before, during):
            _unsafe()
        lines: list[bytes] = []
        line = bytearray()
        total_bytes = 0
        first_chunk = True
        read_size = min(_READ_CHUNK_BYTES, limits.max_line_bytes + 1)
        while True:
            chunk = os.read(fd, read_size)
            if not chunk:
                break
            if first_chunk and chunk.startswith(b"\xef\xbb\xbf"):
                _unsafe()
            first_chunk = False
            total_bytes += len(chunk)
            if total_bytes > limits.max_trace_bytes or total_bytes > remaining_bytes:
                _unsafe()
            for byte in chunk:
                if byte == 0x0D:
                    _unsafe()
                if byte == 0x0A:
                    if not line or len(line) + 1 > limits.max_line_bytes:
                        _unsafe()
                    lines.append(bytes(line) + b"\n")
                    if len(lines) > limits.max_events:
                        _unsafe()
                    line.clear()
                else:
                    line.append(byte)
                    if len(line) + 1 > limits.max_line_bytes:
                        _unsafe()
        if line or not lines:
            _unsafe()
        after = os.fstat(fd)
        if (
            not stat.S_ISREG(after.st_mode)
            or not _same_identity(before, after)
            or after.st_size != before.st_size
            or after.st_mtime_ns != before.st_mtime_ns
            or total_bytes != before.st_size
        ):
            _unsafe()
        return CorpusTrace(trace_path, tuple(lines), total_bytes)
    except (OSError, ValueError):
        _unsafe()
    finally:
        os.close(fd)


def read_corpus(
    trace_paths: Iterable[str],
    *,
    root: Path = CORPUS_ROOT,
    limits: CorpusLimits = DEFAULT_CORPUS_LIMITS,
    budget: CorpusBudget | None = None,
    before_open: Callable[[Path], None] | None = None,
) -> CorpusRead:
    """Descriptor-read direct-child corpus traces under strict framing and budgets.

    This helper is test-data validation evidence only; production ingestion needs
    its own trust-boundary implementation.
    """
    if type(limits) is not CorpusLimits:
        raise ValueError("corpus limits must be CorpusLimits")
    if budget is None:
        budget = CorpusBudget()
    if type(budget) is not CorpusBudget:
        raise ValueError("corpus budget must be CorpusBudget")
    resolved_root = _validated_root(root)
    running_total = budget.total_bytes
    if running_total > limits.max_corpus_bytes:
        _unsafe()
    traces: list[CorpusTrace] = []
    try:
        for trace_path in trace_paths:
            name = _trace_name(trace_path)
            candidate = root / name
            if candidate.parent != root:
                _unsafe()
            if not candidate.resolve(strict=True).is_relative_to(resolved_root):
                _unsafe()
            trace = _read_trace(
                candidate,
                trace_path,
                limits=limits,
                remaining_bytes=limits.max_corpus_bytes - running_total,
                before_open=before_open,
            )
            running_total += trace.total_bytes
            if running_total > limits.max_corpus_bytes:
                _unsafe()
            traces.append(trace)
    except (OSError, TypeError, ValueError):
        _unsafe()
    budget.total_bytes = running_total
    return CorpusRead(tuple(traces), running_total)
