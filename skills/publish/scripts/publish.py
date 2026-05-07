#!/usr/bin/env python
"""Build entry point for the md-publisher publish skill.

Resolves a theme, runs the WeasyPrint or DOCX pipeline (or both),
optionally opens the result. Bootstraps the runtime on first invocation
if needed.

Invocation patterns:
    publish.py source.md
    publish.py source.md --theme atlas --mode dark
    publish.py source.md --format docx
    publish.py source.md --format both
    publish.py source.md --all --format both
    publish.py source.md --output out.pdf --open
"""

from __future__ import annotations

import argparse
import json
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
    ("bloom", "light"), ("bloom", "dark"),
]


def venv_python() -> Path:
    if sys.platform == "win32":
        return RUNTIME_DIR / ".venv" / "Scripts" / "python.exe"
    return RUNTIME_DIR / ".venv" / "bin" / "python"


def _ts_now() -> str:
    """Filesystem-safe local timestamp (mirror of lib/output_paths.timestamp).

    Computed in the parent process so all child runner calls in one
    invocation share the same `.md-publisher/<ts>/` directory.
    """
    import datetime
    return datetime.datetime.now().strftime("%Y%m%d-%H%M%S")


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
    fmt: str,
    ts: str | None,
    explicit_output: Path | None,
    include_cover: bool,
    open_after: bool,
) -> Path:
    """Invoke the venv's Python to run a single build, return output path.

    The actual build runs inside the bootstrapped venv (it has WeasyPrint
    + Pygments + markdown + python-docx). We hand it a JSON job document
    on stdin via `_runner.py` so no user-supplied string is ever
    interpolated into a Python source string — the job dict is passed
    structurally end-to-end.
    """
    runner_script = Path(__file__).resolve().parent / "_runner.py"
    job = {
        "plugin_root":     str(PLUGIN_ROOT),
        "source":          str(source),
        "theme":           theme,
        "mode":            mode,
        "format":          fmt,
        "ts":              ts,
        "explicit_output": str(explicit_output) if explicit_output else None,
        "include_cover":   include_cover,
    }
    py = venv_python()
    proc = subprocess.run(
        [str(py), str(runner_script)],
        input=json.dumps(job),
        capture_output=True, text=True,
    )
    sys.stdout.write(proc.stdout)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        raise SystemExit(f"build failed for theme={theme}, mode={mode}")
    out_line = next(
        (l for l in proc.stdout.splitlines() if l.startswith("OUTPUT_PATH=")), None
    )
    if not out_line:
        raise SystemExit("build runner did not emit OUTPUT_PATH")
    output_path = Path(out_line[len("OUTPUT_PATH="):])
    if open_after:
        open_with_default(output_path)
    return output_path


def _formats_from_arg(fmt_arg: str) -> list[str]:
    """'pdf' | 'docx' -> ['pdf'] / ['docx'];  'both' -> ['pdf', 'docx']."""
    return ["pdf", "docx"] if fmt_arg == "both" else [fmt_arg]


def _validate_explicit_output(args) -> int | None:
    """Sanity-check `--output` extension against `--format`. Returns exit
    code on error, else None."""
    if args.output is None or args.all:
        return None
    if args.format == "both":
        sys.stderr.write(
            "[publish] --output cannot be combined with --format=both "
            "(use the default .md-publisher/<ts>/ convention so PDF and "
            "DOCX land alongside each other)\n"
        )
        return 2
    ext = args.output.suffix.lower()
    expected = "." + args.format
    if ext != expected:
        sys.stderr.write(
            f"[publish] --output {args.output} must have {expected} "
            f"extension when --format={args.format}\n"
        )
        return 2
    return None


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("source", type=Path, help="path to source markdown")
    p.add_argument("--theme", default="default", help="theme name (default, atlas, phosphor, arcade, meridian, tundra, or custom)")
    p.add_argument("--mode", default="light", choices=["light", "dark"],
                   help="theme mode (ignored for mode-less themes)")
    p.add_argument("--output", type=Path, default=None,
                   help="explicit output PDF path (overrides .md-publisher/<ts>/ default)")
    p.add_argument("--all", action="store_true",
                   help="render every built-in (theme x mode) — 6 PDFs (or 12 with --format both)")
    p.add_argument("--no-cover", action="store_true",
                   help="suppress the cover page")
    p.add_argument("--open", action="store_true",
                   help="open the produced PDF(s) with the OS default viewer")
    p.add_argument("--format", default="pdf", choices=["pdf", "docx", "both"],
                   help="output format: pdf (default — current behavior), docx, or both")
    args = p.parse_args()

    src = args.source.resolve()
    if not src.exists():
        sys.stderr.write(f"[publish] source not found: {src}\n")
        return 2

    if not is_bootstrapped():
        rc = run_bootstrap()
        if rc != 0:
            return rc

    rc = _validate_explicit_output(args)
    if rc is not None:
        return rc

    include_cover = not args.no_cover
    formats = _formats_from_arg(args.format)
    outputs: list[Path] = []

    # Single timestamp shared across every output of this invocation, so
    # --format both (and --all --format both) put sibling files into one
    # `.md-publisher/<ts>/` dir instead of scattering across several.
    ts = _ts_now()

    if args.all:
        if args.output:
            sys.stderr.write("[publish] --output ignored when --all is set\n")
        for theme, mode in ALL_BUILTIN_VARIANTS:
            for fmt in formats:
                print(f"\n=== {theme}-{mode} ({fmt}) ===")
                outputs.append(run_one_build(
                    source=src, theme=theme, mode=mode, fmt=fmt, ts=ts,
                    explicit_output=None, include_cover=include_cover,
                    open_after=args.open,
                ))
    else:
        # Default theme has no mode; pass None so it resolves to themes/default/
        mode = None if args.theme == "default" else args.mode
        for fmt in formats:
            outputs.append(run_one_build(
                source=src, theme=args.theme, mode=mode, fmt=fmt, ts=ts,
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
