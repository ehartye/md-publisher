"""First-run bootstrap for md-publisher.

Creates `~/.md-publisher/runtime/` with:
  - .venv/ (Python venv: WeasyPrint, markdown, Pygments, Pillow,
    python-docx, markdown-it-py, cairosvg, pytest)
  - node_modules/ (mermaid-cli + Puppeteer Chromium)

Idempotent: re-runs are no-ops once both pieces are present. Each piece can
be force-rebuilt via flags.

Usage:
    python runtime/bootstrap.py            # bootstrap if missing
    python runtime/bootstrap.py --force    # rebuild both
    python runtime/bootstrap.py --status   # print check results, no install
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path


# Resolve plugin root by walking up from this file: runtime/bootstrap.py -> runtime/ -> plugin
PLUGIN_ROOT = Path(__file__).resolve().parent.parent
USER_DATA_ROOT = Path.home() / ".md-publisher"
RUNTIME_DIR = USER_DATA_ROOT / "runtime"
VENV_DIR = RUNTIME_DIR / ".venv"
NODE_MODULES_DIR = RUNTIME_DIR / "node_modules"


def venv_python() -> Path:
    if sys.platform == "win32":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def mmdc_entry() -> Path:
    return NODE_MODULES_DIR / "@mermaid-js" / "mermaid-cli" / "src" / "cli.js"


def venv_ready() -> bool:
    return venv_python().exists()


def node_ready() -> bool:
    return mmdc_entry().exists()


def ensure_dirs() -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)


def copy_dep_manifests() -> None:
    """Mirror plugin's runtime/{requirements.txt,package.json} into the user runtime."""
    for name in ("requirements.txt", "package.json"):
        src = PLUGIN_ROOT / "runtime" / name
        dst = RUNTIME_DIR / name
        if src.exists():
            shutil.copy2(src, dst)


def create_venv(force: bool = False) -> None:
    if VENV_DIR.exists() and not force:
        return
    if force and VENV_DIR.exists():
        print(f"[bootstrap] removing existing venv at {VENV_DIR}")
        shutil.rmtree(VENV_DIR)
    print(f"[bootstrap] creating venv at {VENV_DIR}")
    venv.create(VENV_DIR, with_pip=True, upgrade_deps=False)


def pip_install() -> None:
    py = venv_python()
    req = RUNTIME_DIR / "requirements.txt"
    print(f"[bootstrap] pip install -r {req.name} (this is the slow step ~30-60s)")
    subprocess.check_call(
        [str(py), "-m", "pip", "install", "--quiet", "--upgrade", "pip"]
    )
    subprocess.check_call(
        [str(py), "-m", "pip", "install", "--quiet", "-r", str(req)]
    )


def npm_install() -> None:
    npm = shutil.which("npm")
    if npm is None:
        sys.stderr.write(
            "\n[bootstrap] ERROR: `npm` not on PATH.\n"
            "Install Node.js 18+ (https://nodejs.org) and re-run.\n\n"
        )
        sys.exit(2)
    print(f"[bootstrap] npm install in {RUNTIME_DIR}")
    print("[bootstrap] this downloads ~170 MB (Puppeteer's Chromium); ~60-120s")
    subprocess.check_call(
        [npm, "install", "--silent", "--no-audit", "--no-fund"],
        cwd=str(RUNTIME_DIR),
    )


def _venv_module_present(module: str, extra_path: str | None = None) -> bool:
    """Check whether the bootstrapped venv can import `module`.

    `extra_path` is prepended to the subprocess's PATH so native deps that
    use ctypes.util.find_library (e.g. cairocffi -> libcairo-2.dll) can
    locate their backing DLLs without a separate add_dll_directory call.
    """
    py = venv_python()
    if not py.exists():
        return False
    env = os.environ.copy()
    if extra_path:
        env["PATH"] = extra_path + os.pathsep + env.get("PATH", "")
    proc = subprocess.run(
        [str(py), "-c", f"import {module}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    return proc.returncode == 0


def _find_gtk_dir() -> Path | None:
    """Return the dir holding GTK DLLs on Windows, else None.

    Mirrors lib/gtk_loader.find_gtk_runtime so bootstrap stays import-free
    (the venv that hosts gtk_loader's deps may not exist yet at this stage).
    """
    if sys.platform != "win32":
        return None
    required = [
        "libgobject-2.0-0.dll",
        "libpango-1.0-0.dll",
        "libcairo-2.dll",
        "libgdk_pixbuf-2.0-0.dll",
    ]
    candidates = [
        os.environ.get("WEASYPRINT_DLL_DIR", ""),
        r"C:\Program Files\GTK3-Runtime Win64\bin",
        r"C:\Program Files\GTK4-Runtime Win64\bin",
        r"C:\msys64\mingw64\bin",
        r"C:\Program Files\Inkscape\bin",
        r"C:\Program Files\GIMP 2\bin",
    ]
    for raw in candidates:
        if not raw:
            continue
        d = Path(raw)
        if all((d / dll).exists() for dll in required):
            return d
    return None


def probe_gtk() -> tuple[bool, str]:
    """On Windows, check whether a GTK runtime is locatable. Returns (ok, hint)."""
    if sys.platform != "win32":
        return True, "GTK is system-managed on this platform; no probe needed."
    gtk_dir = _find_gtk_dir()
    if gtk_dir is not None:
        return True, f"GTK runtime found at: {gtk_dir}"
    return False, (
        "GTK runtime NOT found. WeasyPrint cannot render without it.\n"
        "Easiest fixes (one-time):\n"
        "  - Install Inkscape (https://inkscape.org) — bundles a complete GTK3.\n"
        "  - Or GTK3 Runtime: "
        "https://github.com/tschoonj/GTK-for-Windows-Runtime-Environment-Installer\n"
        "If GTK is somewhere unusual, set WEASYPRINT_DLL_DIR=<gtk-bin-dir>."
    )


def status() -> int:
    print("md-publisher bootstrap status")
    print(f"  user runtime:    {RUNTIME_DIR}")
    print(f"  venv:            {'OK' if venv_ready() else 'MISSING'}  ({venv_python()})")
    print(f"  mmdc:            {'OK' if node_ready() else 'MISSING'}  ({mmdc_entry()})")
    ok, hint = probe_gtk()
    print(f"  gtk:             {'OK' if ok else 'MISSING'}")
    print(f"                   {hint}")

    docx_deps_ok = True
    if venv_ready():
        gtk_dir = _find_gtk_dir()
        gtk_path = str(gtk_dir) if gtk_dir is not None else None
        print("  docx:")
        # cairosvg gets the GTK PATH because cairocffi resolves libcairo-2.dll
        # via ctypes.util.find_library which honors PATH on Windows.
        checks = [
            ("python-docx",    "docx",        None),
            ("markdown-it-py", "markdown_it", None),
            ("cairosvg",       "cairosvg",    gtk_path),
            ("Pillow",         "PIL",         None),
            ("pytest",         "pytest",      None),
        ]
        for label, module, extra in checks:
            present = _venv_module_present(module, extra_path=extra)
            print(f"    {label:<14}  {'OK' if present else 'MISSING'}")
            if not present:
                docx_deps_ok = False
    else:
        print("  docx:            SKIPPED (venv missing)")
        docx_deps_ok = False

    return 0 if (venv_ready() and node_ready() and ok and docx_deps_ok) else 1


def bootstrap(force: bool = False) -> int:
    print(f"md-publisher first-run bootstrap")
    print(f"  user runtime: {RUNTIME_DIR}")
    print(f"  plugin root:  {PLUGIN_ROOT}")

    ensure_dirs()
    copy_dep_manifests()

    if force or not venv_ready():
        create_venv(force=force)
        pip_install()
    else:
        print("[bootstrap] venv already present, skipping (use --force to rebuild)")

    if force or not node_ready():
        npm_install()
    else:
        print("[bootstrap] node_modules already present, skipping (use --force to rebuild)")

    ok, hint = probe_gtk()
    if not ok:
        sys.stderr.write(f"\n[bootstrap] WARNING: {hint}\n\n")
        # Don't fail the bootstrap on missing GTK — first publish call will
        # raise a clear error if it's still missing then.

    print("\n[bootstrap] done.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--force", action="store_true",
                        help="rebuild venv and node_modules even if present")
    parser.add_argument("--status", action="store_true",
                        help="print check results, no install")
    args = parser.parse_args()

    if args.status:
        return status()
    return bootstrap(force=args.force)


if __name__ == "__main__":
    sys.exit(main())
