"""Cross-platform font detection + per-user install for md-publisher themes.

Backs the /md-publisher:install-fonts skill and the publish-docx
font-detection preflight. Per-user install — no admin required on any
platform.

Source for TTFs: github.com/google/fonts (the OFL-licensed catalog).
Each bundled theme uses fonts that live under ofl/<slug>/, mapped via
FONT_SLUGS below. Custom themes naming a Google Font outside this map
need the user to add a mapping or install manually — install_one()
prints a clear "no slug mapping" message in that case.

Per-user install paths:
  - Windows: %LOCALAPPDATA%/Microsoft/Windows/Fonts (no admin); each
    file gets registered in HKCU\\...\\CurrentVersion\\Fonts so apps
    pick it up without a logoff.
  - macOS:   ~/Library/Fonts (loaded by the system on next app launch).
  - Linux:   ~/.local/share/fonts (loaded after `fc-cache -f`, which
    we run automatically when fc-cache is on PATH).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

GH_API = "https://api.github.com/repos/google/fonts/contents/ofl/{slug}"
GH_RAW = "https://raw.githubusercontent.com/google/fonts/main/ofl/{slug}/{file}"


# Map font display name -> github.com/google/fonts/ofl/<slug>
FONT_SLUGS = {
    "Newsreader":          "newsreader",
    "Sora":                "sora",
    "JetBrains Mono":      "jetbrainsmono",
    "IBM Plex Mono":       "ibmplexmono",
    "IBM Plex Sans":       "ibmplexsans",
    "IBM Plex Serif":      "ibmplexserif",
    "Audiowide":           "audiowide",
    "Bungee":              "bungee",
    "Recursive":           "recursive",
    "Playfair Display":    "playfairdisplay",
    "DM Sans":             "dmsans",
    "Fira Code":           "firacode",
    "Cormorant Garamond":  "cormorantgaramond",
    "Karla":               "karla",
    "Source Code Pro":     "sourcecodepro",
    "Barlow Condensed":    "barlowcondensed",
    "Barlow":              "barlow",
    "Inconsolata":         "inconsolata",
}

_GENERIC_CSS_FAMILIES = {
    "serif", "sans-serif", "monospace", "cursive", "fantasy", "system-ui",
}


def _user_fonts_dir() -> Path:
    """Per-user font install dir for the current platform.

    Falls back to ~/AppData/Local/... on Windows when LOCALAPPDATA isn't
    set, and to ~/.local/share/fonts on Linux without ever raising.
    """
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(local) / "Microsoft" / "Windows" / "Fonts"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Fonts"
    return Path.home() / ".local" / "share" / "fonts"


def _system_fonts_dirs() -> list[Path]:
    """System-wide font install dirs to scan for already-installed fonts."""
    if sys.platform == "win32":
        windir = os.environ.get("WINDIR") or r"C:\Windows"
        return [Path(windir) / "Fonts"]
    if sys.platform == "darwin":
        return [Path("/Library/Fonts"), Path("/System/Library/Fonts")]
    return [Path("/usr/share/fonts"), Path("/usr/local/share/fonts")]


def _font_installed(family: str) -> bool:
    """Crude check: any .ttf or .otf in user/system font dirs whose stem
    contains the family name (whitespace-stripped, case-insensitive).

    A single "JetBrains Mono Bold.ttf" is enough to count "JetBrains Mono"
    as installed — Word / WeasyPrint do the same wildcard-y stem match
    when resolving font-family declarations.
    """
    needle = family.replace(" ", "").lower()
    for d in [_user_fonts_dir(), *_system_fonts_dirs()]:
        if not d.exists():
            continue
        for ext in ("*.ttf", "*.otf"):
            for f in d.glob(ext):
                if needle in f.stem.replace(" ", "").lower():
                    return True
    return False


def _families_from_themes(theme_selections: list) -> set[str]:
    """Collect non-generic font family names from a list of ThemeSelection.

    Defensively skips non-string font values — pre-v0.2 user themes can
    have spec.json fonts blocks in the bake-off `{family: "X"}` form
    that lib.theme_loader._fonts_from_user_spec doesn't currently
    unwrap. Warning printed once per offending family so the user knows
    which theme to fix without crashing the install flow.
    """
    needed: set[str] = set()
    for sel in theme_selections:
        if sel.fonts is None:
            continue
        for fam in (sel.fonts.serif, sel.fonts.sans, sel.fonts.mono, sel.fonts.display):
            if not isinstance(fam, str):
                sys.stderr.write(
                    f"  [warn] skipping non-string font value in {sel.slug}: "
                    f"{fam!r} (regenerate this theme via theme-advisor)\n"
                )
                continue
            if fam.lower() not in _GENERIC_CSS_FAMILIES:
                needed.add(fam)
    return needed


def detect_missing_fonts(theme_selections: list) -> list[str]:
    """Given a list of ThemeSelection objects, return the families they
    reference that are NOT currently installed on this machine.
    """
    needed = _families_from_themes(theme_selections)
    return [fam for fam in sorted(needed) if not _font_installed(fam)]


def list_repo_ttfs(slug: str) -> list[str]:
    """List .ttf filenames at github.com/google/fonts/ofl/<slug>/."""
    url = GH_API.format(slug=slug)
    req = urllib.request.Request(url, headers={
        "User-Agent": "md-publisher-font-installer",
        "Accept": "application/vnd.github+json",
    })
    import json as _json
    with urllib.request.urlopen(req, timeout=30) as resp:
        items = _json.loads(resp.read())
    return [it["name"] for it in items if it["name"].lower().endswith(".ttf")]


def download_ttf(slug: str, fname: str, dest: Path) -> None:
    """Stream a single TTF from github.com/google/fonts to `dest`."""
    url = GH_RAW.format(slug=slug, file=fname)
    req = urllib.request.Request(url, headers={
        "User-Agent": "md-publisher-font-installer",
    })
    with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as out:
        shutil.copyfileobj(resp, out)


def _register_font_windows(ttf_path: Path) -> None:
    """Register a per-user font in HKCU so apps pick it up without logoff.

    Windows-only; importing winreg at function scope keeps macOS/Linux
    imports of this module (e.g. for detect_missing_fonts) clean.
    """
    import winreg
    reg_key = r"Software\Microsoft\Windows NT\CurrentVersion\Fonts"
    name = f"{ttf_path.stem} (TrueType)"
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_key,
                        0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, name, 0, winreg.REG_SZ, str(ttf_path))


def _refresh_font_cache_linux() -> None:
    """Run fc-cache against the per-user font dir if fc-cache is available.

    On systems without fontconfig (rare for desktop Linux) the new font
    files are still installed; some apps just won't see them until the
    user runs an equivalent cache-refresh command manually.
    """
    if shutil.which("fc-cache"):
        subprocess.run(
            ["fc-cache", "-f", str(_user_fonts_dir())],
            capture_output=True, text=True,
        )


def install_one(family: str, *, dry_run: bool = False) -> int:
    """Install all variants of a single font family. Returns count of
    files written (0 if already installed, slug-missing, dry-run, or
    download failed).
    """
    if _font_installed(family):
        print(f"  [{family}] already installed - skipping")
        return 0
    slug = FONT_SLUGS.get(family)
    if not slug:
        print(f"  [{family}] no slug mapping - skipping (add to FONT_SLUGS to enable)")
        return 0
    if dry_run:
        print(f"  [{family}] DRY RUN: would install from "
              f"github.com/google/fonts/ofl/{slug}/")
        return 0
    user_fonts = _user_fonts_dir()
    user_fonts.mkdir(parents=True, exist_ok=True)
    try:
        ttfs = list_repo_ttfs(slug)
    except Exception as exc:
        sys.stderr.write(f"  [{family}] FAILED to list repo: {exc}\n")
        return 0
    if not ttfs:
        sys.stderr.write(f"  [{family}] no .ttf files found at ofl/{slug}/\n")
        return 0
    print(f"  [{family}] {len(ttfs)} ttf file(s) -> {user_fonts}")
    n = 0
    for fname in ttfs:
        dest = user_fonts / fname
        try:
            download_ttf(slug, fname, dest)
            if sys.platform == "win32":
                _register_font_windows(dest)
            print(f"    {fname}")
            n += 1
        except Exception as exc:
            sys.stderr.write(f"    FAILED {fname}: {exc}\n")
    if sys.platform == "linux":
        _refresh_font_cache_linux()
    return n


def install_all_for_themes(theme_selections: list, *, dry_run: bool = False) -> int:
    """Install every non-generic font family used by the given themes.

    Returns total count of files written across all families. Idempotent:
    families already present on disk get skipped without a network call.
    """
    needed = _families_from_themes(theme_selections)
    total = 0
    for fam in sorted(needed):
        total += install_one(fam, dry_run=dry_run)
    return total
