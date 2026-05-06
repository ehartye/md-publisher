"""Locate and register a GTK runtime so WeasyPrint can load native deps.

Why this module exists
----------------------
WeasyPrint depends on Pango / Cairo / GDK-Pixbuf / GLib / GObject DLLs.
On Linux these are typically installed via the system package manager
and end up on the dynamic-loader search path. On macOS via Homebrew
they live under `<brew --prefix>/lib`, which the dynamic loader does
NOT search by default on Apple Silicon — `DYLD_LIBRARY_PATH` must be
set explicitly. On Windows, Python 3.8+ deliberately ignores PATH for
native-DLL resolution by default — a security hardening change; the
recommended workaround is `os.add_dll_directory(...)` to whitelist a
specific directory.

This module:
  - On Windows, sniffs well-known GTK install locations and calls
    `os.add_dll_directory` on the first one that contains the required
    DLLs.
  - On macOS, auto-prepends `<brew --prefix>/lib` to DYLD_LIBRARY_PATH
    if brew is on PATH and the dir isn't already there.
  - On Linux, no-op (system loader handles it).

Doing this in code (rather than asking the user to set PATH/dyld vars
or install a separate runtime) drops one of the major papercuts of
running WeasyPrint on macOS + Windows.

Failure mode
------------
If GTK is genuinely absent we cannot produce a PDF — that's a
documented bake-off outcome ("system-level dep missing → result, not
failure" per the spec). We surface a precise error in that case.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


# Required runtime DLLs WeasyPrint touches via cffi. Any directory that
# contains all of these is a valid GTK runtime root.
REQUIRED_DLLS = [
    "libgobject-2.0-0.dll",
    "libpango-1.0-0.dll",
    "libcairo-2.dll",
    "libgdk_pixbuf-2.0-0.dll",
]

# Common locations where a GTK runtime ends up on Windows. Order matters:
# we prefer dedicated GTK installs over bundled ones (less likely to be
# uninstalled out from under us).
WINDOWS_SEARCH_PATHS = [
    r"C:\Program Files\GTK3-Runtime Win64\bin",
    r"C:\Program Files\GTK4-Runtime Win64\bin",
    r"C:\msys64\mingw64\bin",
    r"C:\Program Files\Inkscape\bin",     # Inkscape bundles a full GTK3 stack
    r"C:\Program Files\GIMP 2\bin",       # so does GIMP
]


def _has_required_dlls(directory: Path) -> bool:
    return all((directory / dll).exists() for dll in REQUIRED_DLLS)


def find_gtk_runtime() -> Path | None:
    """Return the first directory holding a complete GTK runtime, or None."""
    # Honor an explicit override first
    override = os.environ.get("WEASYPRINT_DLL_DIR")
    if override:
        candidate = Path(override)
        if _has_required_dlls(candidate):
            return candidate

    if sys.platform != "win32":
        return None

    for raw in WINDOWS_SEARCH_PATHS:
        candidate = Path(raw)
        if _has_required_dlls(candidate):
            return candidate
    return None


def _ensure_macos_dyld_path() -> None:
    """If DYLD_LIBRARY_PATH doesn't include the brew prefix, prepend it.

    On Apple Silicon the dynamic loader does not search /opt/homebrew/lib
    by default; WeasyPrint's CFFI bindings need libpango/libcairo to be
    loadable. Setting DYLD_LIBRARY_PATH from the user's shell rc works
    but is a footgun (every freshly-spawned shell forgets it). Doing it
    here means any Python process that imports gtk_loader gets the right
    env without manual setup.

    No-op when:
      - not on darwin
      - brew isn't on PATH (user has Pango via MacPorts or hand-built)
      - brew --prefix fails to run for any reason
      - the prefix is already on DYLD_LIBRARY_PATH
    """
    if sys.platform != "darwin":
        return
    brew = shutil.which("brew")
    if not brew:
        return
    try:
        proc = subprocess.run(
            [brew, "--prefix"], capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.SubprocessError):
        return
    brew_prefix = proc.stdout.strip()
    if not brew_prefix:
        return
    brew_lib = f"{brew_prefix}/lib"
    existing = os.environ.get("DYLD_LIBRARY_PATH", "")
    if brew_lib in existing.split(":"):
        return
    os.environ["DYLD_LIBRARY_PATH"] = (
        f"{brew_lib}:{existing}" if existing else brew_lib
    )


def register_gtk_runtime() -> Path | None:
    """Find a GTK runtime and make it loadable. Returns the dir on Windows.

    macOS:   prepends `<brew --prefix>/lib` to DYLD_LIBRARY_PATH (returns None).
    Linux:   no-op (system loader handles it; returns None).
    Windows: locates a GTK install and calls os.add_dll_directory.
    """
    # macOS: handle dyld first; other platforms ignore.
    _ensure_macos_dyld_path()

    if sys.platform != "win32":
        # Linux + macOS rely on the (now-augmented) system dynamic loader.
        return None

    gtk_dir = find_gtk_runtime()
    if gtk_dir is None:
        sys.stderr.write(
            "\n[ERROR] No GTK runtime found.\n"
            "WeasyPrint needs Pango, Cairo, GDK-Pixbuf, and GLib at runtime.\n"
            "On Windows the easiest sources are:\n"
            "  - GTK3 Runtime: https://github.com/tschoonj/GTK-for-Windows-Runtime-Environment-Installer\n"
            "  - Or any install of Inkscape or GIMP (their bundled GTK works).\n"
            "After install, set WEASYPRINT_DLL_DIR to the bin/ directory if\n"
            "it isn't on the standard search list.\n\n"
        )
        return None

    os.add_dll_directory(str(gtk_dir))
    return gtk_dir
