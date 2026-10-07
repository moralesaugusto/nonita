"""Sanitisation helpers following NIST SP 800-88 Rev.1.

* **Clear** (logical overwrite): ``shred_file`` overwrites a file in place, flushes it to the device and
  verifies the read-back, then renames, truncates and unlinks it. One pass is what NIST considers sufficient
  for modern drives; ``NONITA_SHRED_PASSES`` can raise it (extra passes: 0xFF, then random).
* **Purge** (cryptographic erase): saved conversations are encrypted (see ``history.py``) and the key file is
  shredded on delete/wipe. This is the NIST-recommended method where overwriting is unreliable (SSD/flash
  wear-leveling, copy-on-write filesystems, journals): the remnants are ciphertext without a key.

Neither can reach backups, snapshots, swap or other copies made outside Nonita.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

_CHUNK = 1024 * 1024
DEFAULT_PASSES = 1


def shred_passes() -> int:
    raw = os.environ.get("NONITA_SHRED_PASSES", "").strip()
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_PASSES
    return value if 1 <= value <= 35 else DEFAULT_PASSES


def _pattern(pass_no: int) -> bytes | None:
    """Fill byte for a pass (None = random). Pass 0: zeros, 1: ones, then random."""
    return {0: b"\x00", 1: b"\xff"}.get(pass_no)


def shred_file(path: str | os.PathLike, passes: int | None = None) -> None:
    """Securely erase *path* (no error if it does not exist)."""
    p = Path(path)
    if p.is_symlink() or not p.is_file():
        p.unlink(missing_ok=True)  # never follow a symlink into someone else's file
        return
    passes = passes or shred_passes()
    size = p.stat().st_size
    try:
        p.chmod(0o600)
        with p.open("r+b", buffering=0) as fh:
            for n in range(passes):
                fill = _pattern(n)
                fh.seek(0)
                left = size
                while left:
                    step = min(_CHUNK, left)
                    fh.write(fill * step if fill else secrets.token_bytes(step))
                    left -= step
                fh.flush()
                os.fsync(fh.fileno())
                if fill and n == passes - 1:  # verify the final deterministic pass
                    fh.seek(0)
                    left = size
                    while left:
                        step = min(_CHUNK, left)
                        if fh.read(step) != fill * step:
                            raise OSError(f"overwrite verification failed for {p.name}")
                        left -= step
            fh.truncate(0)
            os.fsync(fh.fileno())
    finally:
        # Rename first so the original name (often the conversation id/title) is not left in the directory.
        doomed = p.with_name(f".shred-{secrets.token_hex(8)}")
        try:
            p.rename(doomed)
        except OSError:
            doomed = p
        doomed.unlink(missing_ok=True)


def shred_tree(folder: str | os.PathLike) -> None:
    """Shred every file under *folder*, then remove the directories."""
    root = Path(folder)
    if not root.is_dir() or root.is_symlink():
        return
    for item in sorted(root.rglob("*"), key=lambda q: len(q.parts), reverse=True):
        if item.is_dir() and not item.is_symlink():
            try:
                item.rmdir()
            except OSError:
                pass
        else:
            shred_file(item)
    try:
        root.rmdir()
    except OSError:
        pass
