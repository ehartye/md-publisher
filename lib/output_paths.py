"""Output path conventions for md-publisher.

Default output for a build is:

    <source-md-dir>/.md-publisher/<YYYYMMDD-HHMMSS>/<source-stem>.pdf

For preprocess (which rewrites the source in place), the same timestamped
directory holds the backup of the original file as `original.md`. Each
invocation gets a fresh timestamp directory; previous runs are not deleted.

CLI flags `--output <path>` (publish) and `--backup-dir <path>` (preprocess)
override the defaults. When --output is given, only the timestamped subdir
piece changes — the user's path is honored verbatim.
"""

from __future__ import annotations

import datetime
from pathlib import Path


DEFAULT_DIR_NAME = ".md-publisher"


def timestamp() -> str:
    """A filesystem-safe local timestamp like '20260503-141500'."""
    return datetime.datetime.now().strftime("%Y%m%d-%H%M%S")


def default_output_dir(source: Path, ts: str | None = None) -> Path:
    """`<source.parent>/.md-publisher/<timestamp>/`."""
    if ts is None:
        ts = timestamp()
    return source.parent / DEFAULT_DIR_NAME / ts


def derive_output_path(
    source: Path,
    *,
    format: str = "pdf",
    explicit_output: Path | None = None,
    theme_label: str | None = None,
    ts: str | None = None,
) -> Path:
    """Decide where the rendered output should land.

    - If --output is given, honor it verbatim. Caller's responsibility to
      ensure the parent dir exists / can be created and that the extension
      matches `format` (validation happens at the CLI layer).
    - Otherwise: `<source-dir>/.md-publisher/<ts>/<stem>[-<theme>].<ext>`.
      The theme suffix disambiguates multi-render runs (one timestamp,
      many themes/formats).
    """
    if format not in ("pdf", "docx"):
        raise ValueError(f"unsupported format: {format!r}")
    if explicit_output is not None:
        return explicit_output
    out_dir = default_output_dir(source, ts=ts)
    stem = source.stem
    suffix = f"-{theme_label}" if theme_label else ""
    return out_dir / f"{stem}{suffix}.{format}"


# Backwards-compat alias — existing callers use derive_output_pdf
def derive_output_pdf(
    source: Path,
    *,
    explicit_output: Path | None = None,
    theme_label: str | None = None,
    ts: str | None = None,
) -> Path:
    """Deprecated alias for derive_output_path(format='pdf')."""
    return derive_output_path(
        source, format="pdf",
        explicit_output=explicit_output,
        theme_label=theme_label, ts=ts,
    )


def derive_backup_path(
    source: Path,
    *,
    explicit_backup_dir: Path | None = None,
    ts: str | None = None,
) -> Path:
    """Where to back up the original markdown when preprocess rewrites it.

    Default is `<source-dir>/.md-publisher/<ts>/original.md`. The filename
    is always 'original.md' regardless of the source's own name — the
    timestamped dir already disambiguates between sources, and the fixed
    name makes it obvious what the file is when browsing the dir.
    """
    if explicit_backup_dir is not None:
        return explicit_backup_dir / "original.md"
    out_dir = default_output_dir(source, ts=ts)
    return out_dir / "original.md"


def ensure_parent(path: Path) -> Path:
    """Create the parent directory of `path` if absent. Returns the path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    return path
