# md-publisher

A plugin that turns markdown documents (with embedded mermaid diagrams) into themed, searchable, paged PDFs and Microsoft Word DOCX files via WeasyPrint and python-docx.

## Available Skills

| Skill | Purpose |
|-------|---------|
| `publish` | Render markdown to themed PDF, DOCX, or both |
| `preprocess` | LLM-tag mermaid diagrams for theme-aware per-node coloring |
| `theme-advisor` | Interactive Q&A to create a custom theme |
| `theme-gallery` | List and preview built-in + user-installed themes |
| `install-fonts` | Per-platform detect + install of theme fonts for DOCX fidelity |

## Runtime Architecture

On first skill invocation, bootstrap creates `~/.md-publisher/runtime/` containing:
- Python venv (WeasyPrint, markdown, Pygments, Pillow, python-docx, cairosvg)
- Node modules (mermaid-cli + Puppeteer Chromium)

First run takes ~2 minutes and downloads ~250 MB. Subsequent runs reuse the cache.

**System dependency** (cannot be auto-installed): GTK 3 runtime (Pango / Cairo / GDK-Pixbuf) for WeasyPrint's native typography engine. See Troubleshooting below.

## Key Paths

| Path | Contents |
|------|----------|
| `skills/` | Skill definitions (SKILL.md + scripts/) |
| `lib/` | Shared Python pipeline code (PDF, DOCX, theme loader, font installer) |
| `themes/` | 8 bundled themes (atlas/phosphor/arcade/signal × light/dark) + default |
| `runtime/` | Bootstrap script + dependency manifests |
| `~/.md-publisher/` | User data: runtime venv, node_modules, custom themes |

## Common Workflows

1. **Basic publish:** invoke `publish` skill with a markdown file path
2. **Themed publish:** add `--theme atlas --mode dark` (or phosphor/arcade)
3. **All variants:** use `--all` to render every (theme × mode) combination
4. **DOCX output:** use `--format docx` or `--format both`
5. **Pre-color mermaid:** run `preprocess` before `publish` for theme-aware node colors
6. **First DOCX build:** run `install-fonts` once so Word doesn't substitute fonts

## Troubleshooting

If native deps fail, run:
```
python <plugin-root>/runtime/bootstrap.py --doctor
```

This exercises each native dep and prints platform-specific fix instructions:
- **macOS:** `brew install pango cairo gdk-pixbuf libffi`
- **Linux:** `apt install libpango-1.0-0 libcairo2 libgdk-pixbuf2.0-0 libffi-dev`
- **Windows:** Install Inkscape, GIMP, or GTK3 Runtime
