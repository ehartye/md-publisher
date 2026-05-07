"""Tests for skills/publish/scripts/publish.py."""
from __future__ import annotations

import importlib.util
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parent.parent
PUBLISH_SCRIPT = PLUGIN_ROOT / "skills" / "publish" / "scripts" / "publish.py"


def _load_publish_module():
    spec = importlib.util.spec_from_file_location("publish_script", PUBLISH_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_all_builtin_variants_includes_bloom_modes():
    module = _load_publish_module()
    assert ("bloom", "light") in module.ALL_BUILTIN_VARIANTS
    assert ("bloom", "dark") in module.ALL_BUILTIN_VARIANTS
