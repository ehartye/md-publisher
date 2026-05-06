---
name: install-fonts
description: This skill should be used when the user asks to "install theme fonts", "set up md-publisher fonts", "fix font substitution", "install Google Fonts for md-publisher", or invokes /md-publisher:install-fonts. Per-user installs the font families used by the bundled and user-installed themes (Newsreader, Sora, JetBrains Mono, IBM Plex Mono, Audiowide, Bungee, Recursive) without requiring administrator privileges. Cross-platform, Windows (HKCU registry), macOS (~/Library/Fonts), Linux (~/.local/share/fonts + fc-cache).
---

# install-fonts

Install the Google Fonts that md-publisher's bundled themes use, so DOCX output renders with the intended typography instead of falling back to system substitutes.

PDF output via WeasyPrint embeds the typeface in the file regardless, so this skill mostly matters for DOCX (Word names fonts but doesn't embed them).

## When to use

- "install theme fonts" / "fix font substitution"
- "set up md-publisher fonts"
- explicit invocation: `/md-publisher:install-fonts`
- after the publish skill warns about missing fonts ("phosphor needs IBM Plex Mono — Word will substitute")

## Workflow

1. **Determine scope.** If the user passed `--theme <name> --mode <mode>`, install only that theme's fonts. If they passed `--all` or no flag, install fonts for all themes installed (bundled + user themes at `~/.md-publisher/themes/`).

2. **Run the installer.** Invoke `${CLAUDE_PLUGIN_ROOT}/skills/install-fonts/scripts/install-fonts.py` with the parsed flags. The script:
   - Resolves the requested theme(s) via `lib.theme_loader.resolve_selection`.
   - Calls `lib.font_install.install_all_for_themes()` which downloads TTFs from `github.com/google/fonts/ofl/<slug>/` and installs them per-user.
   - On Windows: copies to `%LOCALAPPDATA%\Microsoft\Windows\Fonts` and registers in HKCU.
   - On macOS: copies to `~/Library/Fonts`.
   - On Linux: copies to `~/.local/share/fonts` and runs `fc-cache` (when available).
   - Idempotent — skips fonts already present.
   - `--dry-run` lists what would be installed without downloading.

3. **Report results.** Print the per-family install count and a reminder that open apps must be re-launched to pick up newly-registered fonts.

## Flags

| Flag | Default | Meaning |
|---|---|---|
| `--theme <name>` | (all themes) | Restrict to one theme name. |
| `--mode <light\|dark>` | (both) | Restrict to one mode within --theme. |
| `--all` | off | Explicit "all themes" — equivalent to no theme/mode flags. |
| `--dry-run` | off | List what would be installed; download nothing. |

## Examples

```bash
# Install fonts for everything currently installed
/md-publisher:install-fonts

# Just phosphor's font (IBM Plex Mono)
/md-publisher:install-fonts --theme phosphor

# Preview without installing
/md-publisher:install-fonts --dry-run
```

## Failure modes

- **No internet** — github.com download will fail; surface the error and tell the user to retry once online.
- **Slug missing for a custom-theme font** — `lib.font_install.FONT_SLUGS` only knows the bundled themes' fonts. Custom themes naming a Google Font outside that map need the user to add a mapping or install manually. Surface the missing slug clearly.
- **Per-user install dir not writable** — rare; surface the OS error.
