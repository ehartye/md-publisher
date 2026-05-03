"""Runtime locator + bootstrap-state checks.

The plugin's heavy deps (WeasyPrint, mermaid-cli, Puppeteer Chromium) live
under `~/.md-publisher/runtime/`, populated by `runtime/bootstrap.py` on
first run. This module finds those deps and reports whether bootstrap is
required.

Layout under USER_DATA_ROOT:

    ~/.md-publisher/
    ├── runtime/
    │   ├── .venv/                    Python venv (WeasyPrint, markdown, Pygments)
    │   ├── node_modules/             mermaid-cli + Puppeteer Chromium
    │   ├── package.json              copied from plugin runtime/ at bootstrap
    │   └── requirements.txt          copied from plugin runtime/ at bootstrap
    └── themes/                       user-installed custom themes
        └── <slug>/
            ├── style.css
            ├── mermaid-config.json
            └── spec.json
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


USER_DATA_ROOT = Path.home() / ".md-publisher"
RUNTIME_DIR = USER_DATA_ROOT / "runtime"
USER_THEMES_DIR = USER_DATA_ROOT / "themes"
VENV_DIR = RUNTIME_DIR / ".venv"
NODE_MODULES_DIR = RUNTIME_DIR / "node_modules"


def plugin_root() -> Path:
    """The plugin install dir — looked up via $CLAUDE_PLUGIN_ROOT.

    Falls back to walking upward from this file when the env var is
    absent (useful during plugin development / direct script invocation).
    """
    env = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if env:
        return Path(env).resolve()
    # lib/runtime.py -> lib/ -> plugin root
    return Path(__file__).resolve().parent.parent


def venv_python() -> Path:
    """Path to the bootstrapped venv's Python interpreter."""
    if sys.platform == "win32":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def mmdc_entry() -> Path:
    """Path to mermaid-cli's main module (invoked via `node <path>`).

    We call mmdc as `node node_modules/@mermaid-js/mermaid-cli/src/cli.js`
    rather than via the .cmd shim because Windows shim quoting is fragile.
    """
    return NODE_MODULES_DIR / "@mermaid-js" / "mermaid-cli" / "src" / "cli.js"


def puppeteer_config() -> Path:
    """Path to the puppeteer config mmdc consumes.

    Default: `runtime/puppeteer.json` with empty args (Chromium sandbox enabled).

    Override: set MD_PUBLISHER_DISABLE_SANDBOX=1 to use the no-sandbox variant
    (`runtime/puppeteer-no-sandbox.json`). Required in CI / containers / WSL
    where the user-namespace prerequisites for Chromium's sandbox are absent.
    NEVER set this env var on a multi-user system — disabling the sandbox
    drops a primary defense layer if a malicious mermaid block is processed.
    """
    if os.environ.get("MD_PUBLISHER_DISABLE_SANDBOX") == "1":
        return plugin_root() / "runtime" / "puppeteer-no-sandbox.json"
    return plugin_root() / "runtime" / "puppeteer.json"


def is_bootstrapped() -> bool:
    """True iff both Python venv and node_modules/mermaid-cli are present."""
    return venv_python().exists() and mmdc_entry().exists()


def bootstrap_status() -> dict:
    """Return a dict of checks for diagnostic output.

    Keys: venv (bool), mmdc (bool), gtk (bool, Windows-only — None elsewhere),
    plus the resolved paths for each.
    """
    status = {
        "venv": venv_python().exists(),
        "venv_path": str(venv_python()),
        "mmdc": mmdc_entry().exists(),
        "mmdc_path": str(mmdc_entry()),
        "gtk": None,
        "gtk_hint": None,
    }
    if sys.platform == "win32":
        # Light GTK probe: check whether the gtk_loader module finds a runtime.
        # Actual loading happens at WeasyPrint-import time; this is just a hint.
        try:
            from .gtk_loader import find_gtk_runtime  # type: ignore
            gtk_dir = find_gtk_runtime()
            status["gtk"] = gtk_dir is not None
            status["gtk_hint"] = (
                str(gtk_dir) if gtk_dir
                else "no GTK runtime detected; install Inkscape/GIMP or set WEASYPRINT_DLL_DIR"
            )
        except Exception as e:
            status["gtk"] = False
            status["gtk_hint"] = f"GTK probe errored: {e}"
    return status
