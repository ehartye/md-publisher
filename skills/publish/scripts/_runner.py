"""Internal worker invoked by publish.py inside the bootstrapped venv.

Reads a JSON job document from stdin so no user-supplied value is ever
interpolated into a Python source string. Writes the resolved output PDF
path to stdout (single line `OUTPUT_PATH=<path>` for the parent to parse).

Job document schema:
    {
      "plugin_root":     "/abs/path/to/plugin",
      "source":          "/abs/path/to/source.md",
      "theme":           "atlas",
      "mode":            "light" | null,
      "explicit_output": "/abs/path/to/out.pdf" | null,
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

    # Add the plugin root to sys.path so `from lib import ...` works
    sys.path.insert(0, job["plugin_root"])
    from lib import theme_loader, output_paths, pipeline  # noqa: E402

    sel = theme_loader.resolve_selection(name=job["theme"], mode=job["mode"])
    src = Path(job["source"])
    explicit_output = (
        Path(job["explicit_output"]) if job["explicit_output"] else None
    )
    label = sel.slug if sel.name != "default" else None
    out = output_paths.derive_output_pdf(
        src, explicit_output=explicit_output, theme_label=label
    )
    output_paths.ensure_parent(out)

    pipeline.build_pdf(
        source=src,
        output=out,
        theme_selection=sel,
        include_cover=bool(job.get("include_cover", True)),
    )
    print(f"OUTPUT_PATH={out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
