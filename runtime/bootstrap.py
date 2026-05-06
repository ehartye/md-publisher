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
    python runtime/bootstrap.py --doctor   # diagnose native-dep failures
                                           # with platform-specific fixes
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
    """Check whether the GTK/Pango/Cairo native deps are locatable.

    Returns (ok, hint).

    Windows: scans well-known install dirs for the GTK DLLs.
    macOS:   checks `<brew --prefix>/lib/libpango-1.0.0.dylib` if brew is on
             PATH; warns if not (Homebrew is the standard install path).
    Linux:   checks `ldconfig -p` for libpango-1.0; falls back to a stat
             of /usr/lib*/libpango-1.0.so* if ldconfig isn't available.

    For a deeper check (actually load WeasyPrint and render a PDF), run
    `--doctor` instead — that catches dyld-can't-find-it failures the
    static probes here can't.
    """
    if sys.platform == "win32":
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
    if sys.platform == "darwin":
        brew = shutil.which("brew")
        if not brew:
            return False, (
                "Homebrew not on PATH. WeasyPrint needs Pango/Cairo via brew:\n"
                "  brew install pango cairo gdk-pixbuf libffi\n"
                "(See https://brew.sh to install Homebrew.)"
            )
        try:
            proc = subprocess.run(
                [brew, "--prefix"], capture_output=True, text=True, timeout=5
            )
            brew_prefix = proc.stdout.strip() or "/opt/homebrew"
        except (OSError, subprocess.SubprocessError):
            brew_prefix = "/opt/homebrew"
        # Pango installs as libpango-1.0.dylib; check the real-life filename.
        candidates = [
            Path(brew_prefix) / "lib" / "libpango-1.0.dylib",
            Path(brew_prefix) / "lib" / "libpango-1.0.0.dylib",
        ]
        if any(p.exists() for p in candidates):
            return True, f"Pango/Cairo found via Homebrew at: {brew_prefix}/lib"
        return False, (
            f"Pango not found at {brew_prefix}/lib. Install with:\n"
            "  brew install pango cairo gdk-pixbuf libffi\n"
            "Then re-run --status. If `--doctor` still fails after install,\n"
            "the dyld loader may not search the brew prefix; the plugin auto-\n"
            "prepends DYLD_LIBRARY_PATH but SIP-protected Python (e.g. the\n"
            "system /usr/bin/python3) ignores it. Use brew/pyenv Python."
        )
    # Linux
    if shutil.which("ldconfig"):
        try:
            proc = subprocess.run(
                ["ldconfig", "-p"], capture_output=True, text=True, timeout=5
            )
            if "libpango-1.0" in proc.stdout:
                return True, "Pango/Cairo present in ldconfig cache."
        except (OSError, subprocess.SubprocessError):
            pass
    # Fallback: stat the lib in standard locations
    for d in ("/usr/lib", "/usr/lib/x86_64-linux-gnu", "/usr/lib64"):
        for name in ("libpango-1.0.so.0", "libpango-1.0.so"):
            if (Path(d) / name).exists():
                return True, f"Pango present at {d}/{name}"
    return False, (
        "Pango not found. Install via your package manager:\n"
        "  Debian/Ubuntu: sudo apt install libpango-1.0-0 libcairo2 libgdk-pixbuf2.0-0 libffi-dev\n"
        "  Fedora/RHEL:   sudo dnf install pango cairo gdk-pixbuf2 libffi\n"
        "  Arch:          sudo pacman -S pango cairo gdk-pixbuf2 libffi"
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


def _print_macos_weasyprint_fix() -> None:
    """Print actionable macOS Homebrew install + dyld fix instructions."""
    has_brew = bool(shutil.which("brew"))
    print("\n  Suggested fix (macOS):")
    if not has_brew:
        print("    1. Install Homebrew: https://brew.sh")
    print("    2. brew install pango cairo gdk-pixbuf libffi")
    if has_brew:
        try:
            brew_prefix = subprocess.run(
                ["brew", "--prefix"], capture_output=True, text=True, timeout=5
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            brew_prefix = "/opt/homebrew"
        print( "    3. If WeasyPrint still fails, the dyld loader can't find brew libs.")
        print(f"       Apple Silicon (M-series): brew prefix is {brew_prefix} (typically /opt/homebrew)")
        print( "       Intel macOS: brew prefix is /usr/local")
        print(f"       Workaround: set DYLD_LIBRARY_PATH=\"{brew_prefix}/lib:$DYLD_LIBRARY_PATH\" in your shell rc")
        print( "       Plugin auto-prepends this when brew is on PATH — verify by re-running --doctor.")


def _print_linux_weasyprint_fix() -> None:
    print("\n  Suggested fix (Linux):")
    print("    Debian/Ubuntu: sudo apt install libpango-1.0-0 libcairo2 libgdk-pixbuf2.0-0 libffi-dev")
    print("    Fedora/RHEL:   sudo dnf install pango cairo gdk-pixbuf2 libffi")
    print("    Arch:          sudo pacman -S pango cairo gdk-pixbuf2 libffi")


def _print_windows_weasyprint_fix() -> None:
    print("\n  Suggested fix (Windows):")
    print("    GTK 3 runtime not found. Easiest source: install Inkscape (https://inkscape.org)")
    print("    or GIMP — they bundle a complete GTK3 stack the plugin auto-detects.")
    print("    Alternative: GTK3 Runtime installer at")
    print("      https://github.com/tschoonj/GTK-for-Windows-Runtime-Environment-Installer")
    print("    If GTK lives somewhere unusual, set WEASYPRINT_DLL_DIR=<gtk-bin-dir>.")


def _run_doctor() -> int:
    """Diagnose native-dep loading issues and print actionable fixes.

    Returns 0 if all native deps load cleanly, 1 if any fail.

    Probes (in order):
      1. WeasyPrint full PDF write — forces native libpango/libcairo
         resolution beyond mere `import weasyprint`.
      2. cairosvg svg2png — shares Cairo with WeasyPrint, exercised
         independently because the DOCX pipeline uses it directly.
      3. mmdc + Chromium — runs `node mmdc --version`.
      4. Per-user font dir writability — only if lib.font_install
         exists (Task 4.2; gracefully skipped before then).
    """
    print(f"[doctor] platform={sys.platform}")
    failures: list[str] = []

    if not venv_ready():
        print("\n[doctor] venv missing — run `python runtime/bootstrap.py` first.")
        return 1

    py = venv_python()

    # All native-dep probes run with the plugin root on sys.path so they
    # can import lib.gtk_loader and call register_gtk_runtime() the same
    # way the production pipelines do — that's the function that calls
    # os.add_dll_directory on Windows and prepends to DYLD_LIBRARY_PATH
    # on macOS. Without it the probes get false negatives.
    sys_path_prelude = (
        f"import sys\nsys.path.insert(0, r'{PLUGIN_ROOT}')\n"
        "from lib.gtk_loader import register_gtk_runtime\n"
        "register_gtk_runtime()\n"
    )

    # 1. WeasyPrint — full PDF write forces native lib load
    print("\n[doctor] Probing WeasyPrint native deps...")
    weasy_probe = sys_path_prelude + (
        "try:\n"
        "    import weasyprint\n"
        "    weasyprint.HTML(string='<p>x</p>').write_pdf()\n"
        "    print('OK')\n"
        "except OSError as e:\n"
        "    sys.stderr.write(f'NATIVE LIB MISSING: {e}\\n'); sys.exit(2)\n"
        "except Exception as e:\n"
        "    sys.stderr.write(f'OTHER: {type(e).__name__}: {e}\\n'); sys.exit(3)\n"
    )
    proc = subprocess.run(
        [str(py), "-c", weasy_probe], capture_output=True, text=True
    )
    if proc.returncode == 0:
        print("  WeasyPrint: OK")
    else:
        failures.append("weasyprint")
        print(f"  WeasyPrint: FAILED — {proc.stderr.strip()}")
        if sys.platform == "darwin":
            _print_macos_weasyprint_fix()
        elif sys.platform == "linux":
            _print_linux_weasyprint_fix()
        else:
            _print_windows_weasyprint_fix()

    # 2. cairosvg — shares Cairo with WeasyPrint but uses a different
    # loader path. cairocffi calls ctypes.util.find_library which honors
    # PATH on Windows (NOT os.add_dll_directory), so register_gtk_runtime
    # alone isn't enough — we also prepend the GTK dir to PATH in env.
    # SVG must declare a size; cairosvg refuses to rasterize an unsized
    # element. A 1x1 transparent SVG is enough to force native lib load.
    print("\n[doctor] Probing cairosvg...")
    cairo_env = os.environ.copy()
    gtk_dir = _find_gtk_dir()
    if gtk_dir is not None:
        cairo_env["PATH"] = str(gtk_dir) + os.pathsep + cairo_env.get("PATH", "")
    cairo_probe = (
        "import cairosvg\n"
        "svg = b'<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"1\" height=\"1\"/>'\n"
        "cairosvg.svg2png(bytestring=svg)\n"
        "print('OK')\n"
    )
    proc = subprocess.run(
        [str(py), "-c", cairo_probe],
        capture_output=True, text=True, env=cairo_env,
    )
    if proc.returncode == 0:
        print("  cairosvg: OK")
    else:
        failures.append("cairosvg")
        print(f"  cairosvg: FAILED — {proc.stderr.strip()}")
        print("    (cairosvg shares Cairo with WeasyPrint; same fix applies.)")

    # 3. mmdc + Chromium
    print("\n[doctor] Probing mmdc...")
    mmdc = mmdc_entry()
    if not mmdc.exists():
        failures.append("mmdc")
        print(f"  mmdc: FAILED — not found at {mmdc}")
        print("    Fix: re-run `python runtime/bootstrap.py --force`")
    else:
        node = shutil.which("node")
        if node is None:
            failures.append("mmdc")
            print("  mmdc: FAILED — `node` not on PATH (need Node.js 18+)")
        else:
            try:
                proc = subprocess.run(
                    [node, str(mmdc), "--version"],
                    capture_output=True, text=True, timeout=30,
                )
            except subprocess.TimeoutExpired:
                failures.append("mmdc")
                print("  mmdc: FAILED — --version timed out after 30s")
            else:
                if proc.returncode == 0:
                    print(f"  mmdc: OK (version: {proc.stdout.strip()})")
                else:
                    failures.append("mmdc")
                    err = proc.stderr.strip()
                    print(f"  mmdc: FAILED — {err}")
                    if "ENOENT" in err or "Cannot find module" in err:
                        print("    Fix: re-run `python runtime/bootstrap.py --force`")
                    elif "browser" in err.lower() or "chromium" in err.lower():
                        print("    Fix: set MD_PUBLISHER_DISABLE_SANDBOX=1 (Linux/CI/WSL only)")

    # 4. Per-user font dir writability (only when font_install module exists)
    print("\n[doctor] Probing per-user font dir...")
    probe_fontdir_via = (
        "import sys, pathlib\n"
        "sys.path.insert(0, r'" + str(PLUGIN_ROOT) + "')\n"
        "try:\n"
        "    from lib.font_install import _user_fonts_dir\n"
        "except ImportError:\n"
        "    print('SKIP'); sys.exit(0)\n"
        "fdir = _user_fonts_dir()\n"
        "fdir.mkdir(parents=True, exist_ok=True)\n"
        "p = fdir / '.md-publisher-doctor-probe'\n"
        "p.write_text('ok'); p.unlink()\n"
        "print('OK', fdir)\n"
    )
    proc = subprocess.run(
        [str(py), "-c", probe_fontdir_via], capture_output=True, text=True
    )
    if proc.returncode == 0:
        out = proc.stdout.strip()
        if out == "SKIP":
            print("  font dir: SKIPPED (lib.font_install not yet shipped — Task 4.2)")
        else:
            print(f"  font dir: {out}")
    else:
        failures.append("font_dir")
        print(f"  font dir: FAILED — {proc.stderr.strip()}")
        print("    Fix: ensure ~/Library/Fonts (mac), ~/.local/share/fonts (Linux),")
        print("         or %LOCALAPPDATA%/Microsoft/Windows/Fonts (Win) is writable.")

    print()
    if failures:
        print(f"[doctor] {len(failures)} issue(s): {', '.join(failures)}")
        return 1
    print("[doctor] all checks pass.")
    return 0


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
    parser.add_argument("--doctor", action="store_true",
                        help="diagnose native dep loading issues "
                             "(Pango/Cairo/GDK-Pixbuf, mmdc, font dir) "
                             "and print actionable platform-specific fixes")
    args = parser.parse_args()

    if args.doctor:
        return _run_doctor()
    if args.status:
        return status()
    return bootstrap(force=args.force)


if __name__ == "__main__":
    sys.exit(main())
