"""Locate and register a GTK runtime on Windows so WeasyPrint can load.

Why this module exists
----------------------
WeasyPrint depends on Pango / Cairo / GDK-Pixbuf / GLib / GObject DLLs.
On Linux and macOS these are typically installed via the system package
manager and end up on the dynamic-loader search path. On Windows,
Python 3.8+ deliberately ignores PATH for native-DLL resolution by
default — a security hardening change. The recommended workaround is
`os.add_dll_directory(...)` to whitelist a specific directory.

This module sniffs a list of well-known install locations for GTK and
calls add_dll_directory on the first one that contains the required
DLLs. If none are found we raise a clear error pointing the user at
the GTK installer. Doing this in code (rather than asking the user
to set PATH or install a separate runtime) drops one of the major
papercuts of running WeasyPrint on Windows.

Failure mode
------------
If GTK is genuinely absent we cannot produce a PDF — that's a
documented bake-off outcome ("system-level dep missing → result, not
failure" per the spec). We surface a precise error in that case.
"""

from __future__ import annotations

import os
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


def register_gtk_runtime() -> Path | None:
    """Find a GTK runtime and add it to the DLL search path. Returns the dir."""
    if sys.platform != "win32":
        # Other platforms rely on the system dynamic loader; nothing to do.
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
