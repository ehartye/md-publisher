#!/usr/bin/env python
"""Build entry point for the md-publisher publish skill.

Resolves a theme, runs the WeasyPrint pipeline, optionally opens the
result. Bootstraps the runtime on first invocation if needed.

Invocation patterns:
    publish.py source.md
    publish.py source.md --theme atlas --mode dark
    publish.py source.md --all
    publish.py source.md --output out.pdf --open
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


PLUGIN_ROOT = Path(os.environ.get("CLAUDE_PLUGIN_ROOT") or
                   Path(__file__).resolve().parent.parent.parent.parent)
USER_DATA_ROOT = Path.home() / ".md-publisher"
RUNTIME_DIR = USER_DATA_ROOT / "runtime"


# All built-in theme×mode combinations (used for --all)
ALL_BUILTIN_VARIANTS = [
    ("atlas", "light"), ("atlas", "dark"),
    ("phosphor", "light"), ("phosphor", "dark"),
    ("arcade", "light"), ("arcade", "dark"),
]


def venv_python() -> Path:
    if sys.platform == "win32":
        return RUNTIME_DIR / ".venv" / "Scripts" / "python.exe"
    return RUNTIME_DIR / ".venv" / "bin" / "python"


def is_bootstrapped() -> bool:
    py = venv_python()
    mmdc = (RUNTIME_DIR / "node_modules" / "@mermaid-js" / "mermaid-cli" / "src" / "cli.js")
    return py.exists() and mmdc.exists()


def run_bootstrap() -> int:
    """Run the plugin's bootstrap script with the system Python."""
    bootstrap = PLUGIN_ROOT / "runtime" / "bootstrap.py"
    if not bootstrap.exists():
        sys.stderr.write(f"[publish] bootstrap script missing at {bootstrap}\n")
        return 2
    print("[publish] first-run bootstrap starting (~2 min, ~250 MB download)")
    return subprocess.call([sys.executable, str(bootstrap)])


def open_with_default(path: Path) -> None:
    """OS-default open. Best-effort — failures here aren't fatal."""
    try:
        if sys.platform == "win32":
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.call(["open", str(path)])
        else:
            subprocess.call(["xdg-open", str(path)])
    except Exception as e:
        sys.stderr.write(f"[publish] could not open {path}: {e}\n")


def run_one_build(
    *,
    source: Path,
    theme: str,
    mode: str | None,
    explicit_output: Path | None,
    include_cover: bool,
    open_after: bool,
) -> Path:
    """Invoke the venv's Python to run a single build, return output path.

    The actual build happens in the bootstrapped venv — this script runs
    under the system Python (since the venv has WeasyPrint which the system
    python may not), but the heavy work runs over there via a small inline
    runner so the venv's deps are in scope.
    """
    runner_code = (
        "import sys; sys.path.insert(0, r'" + str(PLUGIN_ROOT) + "');\n"
        "from lib import theme_loader, output_paths, pipeline;\n"
        "from pathlib import Path;\n"
        f"sel = theme_loader.resolve_selection(name={theme!r}, mode={mode!r});\n"
        f"src = Path(r{str(source)!r});\n"
        f"explicit = " + (f"Path(r{str(explicit_output)!r})" if explicit_output else "None") + ";\n"
        f"label = sel.slug if sel.name != 'default' else None;\n"
        "out = output_paths.derive_output_pdf(src, explicit_output=explicit, theme_label=label);\n"
        "output_paths.ensure_parent(out);\n"
        f"pipeline.build_pdf(source=src, output=out, theme_selection=sel, include_cover={include_cover!r});\n"
        "print('OUTPUT_PATH=' + str(out));\n"
    )
    py = venv_python()
    proc = subprocess.run(
        [str(py), "-c", runner_code],
        capture_output=True, text=True,
    )
    sys.stdout.write(proc.stdout)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        raise SystemExit(f"build failed for theme={theme}, mode={mode}")
    # Parse the OUTPUT_PATH line printed by the runner
    out_line = next(
        (l for l in proc.stdout.splitlines() if l.startswith("OUTPUT_PATH=")), None
    )
    if not out_line:
        raise SystemExit("build runner did not emit OUTPUT_PATH")
    output_path = Path(out_line[len("OUTPUT_PATH="):])
    if open_after:
        open_with_default(output_path)
    return output_path


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("source", type=Path, help="path to source markdown")
    p.add_argument("--theme", default="default", help="theme name (default, atlas, phosphor, arcade, or custom)")
    p.add_argument("--mode", default="light", choices=["light", "dark"],
                   help="theme mode (ignored for mode-less themes)")
    p.add_argument("--output", type=Path, default=None,
                   help="explicit output PDF path (overrides .md-publisher/<ts>/ default)")
    p.add_argument("--all", action="store_true",
                   help="render every built-in (theme x mode) — 6 PDFs")
    p.add_argument("--no-cover", action="store_true",
                   help="suppress the cover page")
    p.add_argument("--open", action="store_true",
                   help="open the produced PDF(s) with the OS default viewer")
    args = p.parse_args()

    src = args.source.resolve()
    if not src.exists():
        sys.stderr.write(f"[publish] source not found: {src}\n")
        return 2

    if not is_bootstrapped():
        rc = run_bootstrap()
        if rc != 0:
            return rc

    include_cover = not args.no_cover
    outputs: list[Path] = []

    if args.all:
        if args.output:
            sys.stderr.write("[publish] --output ignored when --all is set\n")
        for theme, mode in ALL_BUILTIN_VARIANTS:
            print(f"\n=== {theme}-{mode} ===")
            outputs.append(run_one_build(
                source=src, theme=theme, mode=mode,
                explicit_output=None, include_cover=include_cover,
                open_after=args.open,
            ))
    else:
        # Default theme has no mode; pass None so it resolves to themes/default/
        mode = None if args.theme == "default" else args.mode
        outputs.append(run_one_build(
            source=src, theme=args.theme, mode=mode,
            explicit_output=args.output, include_cover=include_cover,
            open_after=args.open,
        ))

    print("\n[publish] done. Outputs:")
    for path in outputs:
        size_kb = path.stat().st_size // 1024 if path.exists() else 0
        print(f"  {path} ({size_kb} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
