"""Internal worker invoked by publish.py inside the bootstrapped venv.

Reads a JSON job document from stdin so no user-supplied value is ever
interpolated into a Python source string. Writes the resolved output
path to stdout (single line `OUTPUT_PATH=<path>` for the parent to parse).

Job document schema:
    {
      "plugin_root":     "/abs/path/to/plugin",
      "source":          "/abs/path/to/source.md",
      "theme":           "atlas",
      "mode":            "light" | null,
      "format":          "pdf" | "docx",
      "explicit_output": "/abs/path/to/out.<ext>" | null,
      "include_cover":   true
    }

This file is normally only run by publish.py — never directly by a user.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    job = json.load(sys.stdin)
    fmt = job.get("format", "pdf")
    if fmt not in ("pdf", "docx"):
        sys.stderr.write(f"[runner] unsupported format: {fmt!r}\n")
        return 2

    # Add the plugin root to sys.path so `from lib import ...` works
    sys.path.insert(0, job["plugin_root"])
    from lib import theme_loader, output_paths  # noqa: E402

    sel = theme_loader.resolve_selection(name=job["theme"], mode=job["mode"])
    src = Path(job["source"])
    explicit_output = (
        Path(job["explicit_output"]) if job["explicit_output"] else None
    )
    label = sel.slug if sel.name != "default" else None
    out = output_paths.derive_output_path(
        src,
        output_format=fmt,
        explicit_output=explicit_output,
        theme_label=label,
        ts=job.get("ts"),
    )
    output_paths.ensure_parent(out)
    include_cover = bool(job.get("include_cover", True))

    if fmt == "pdf":
        from lib import pipeline  # noqa: E402
        pipeline.build_pdf(
            source=src,
            output=out,
            theme_selection=sel,
            include_cover=include_cover,
        )
    else:  # docx
        # The DOCX path requires structured palette + fonts on the
        # ThemeSelection. The 'default' theme has no per-mode palette
        # in spec data, so it can't be rendered to DOCX.
        if sel.palette is None or sel.fonts is None:
            sys.stderr.write(
                f"[runner] theme {sel.slug!r} has no palette/fonts data; "
                f"DOCX requires a themed selection (atlas/phosphor/arcade "
                f"with --mode, or a user theme with palette+fonts in "
                f"spec.json). The 'default' theme is PDF-only.\n"
            )
            return 2

        # Font availability preflight — warn the user (don't fail) so
        # they know substitutions will happen if fonts are missing.
        try:
            from lib import font_install  # noqa: E402
            missing = font_install.detect_missing_fonts([sel])
        except Exception:
            missing = []  # detect_missing_fonts is best-effort
        if missing:
            mode_arg = f" --mode {sel.mode}" if sel.mode else ""
            sys.stderr.write(
                f"[publish] WARNING: missing fonts for {sel.slug}: "
                f"{', '.join(missing)}. Word will substitute. "
                f"Run /md-publisher:install-fonts --theme {sel.name}"
                f"{mode_arg} to fix.\n"
            )

        from lib import docx_pipeline  # noqa: E402
        docx_pipeline.build_docx(
            source=src,
            output=out,
            theme_selection=sel,
            include_cover=include_cover,
        )

    print(f"OUTPUT_PATH={out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
