# md-publisher

A Claude Code plugin that turns markdown documents — with embedded mermaid diagrams — into themed, searchable, paged PDFs. Built on WeasyPrint after a four-toolchain bake-off (see `~/repos/md-publisher-bakeoff/` for the comparison).

## What you get

Four skills, six bundled themes, and a Python pipeline that produces print-ready PDFs with searchable mermaid text.

| Skill | What it does | Invoke as |
|---|---|---|
| **`publish`** | Turn a markdown file into a themed PDF. | `/md-publisher:publish doc.md [--theme atlas --mode light] [--all] [--open] [--no-cover]` |
| **`preprocess`** | LLM-tag mermaid diagrams (`:::ingress / :::core / :::transform / :::bridge`) so themes color nodes by role. Rewrites the source in place; original gets backed up. | `/md-publisher:preprocess doc.md [--add-frontmatter]` |
| **`theme-advisor`** | Interactive Q&A flow that produces a custom theme module under `~/.md-publisher/themes/<name>/`. | `/md-publisher:theme-advisor` |
| **`theme-gallery`** | List, preview, and pick from built-in + user-installed themes in one combined view. | `/md-publisher:theme-gallery` |

## Themes

Six bundled themes (each in light + dark): **atlas** (corporate, customer-facing), **phosphor** (developer, terminal aesthetic), **arcade** (gamer, manual aesthetic), and **default** (clean editorial). Custom themes go to `~/.md-publisher/themes/<slug>/`. The gallery resolver checks user themes first, falls back to built-ins.

## Output convention

`<source-md-dir>/.md-publisher/<YYYYMMDD-HHMMSS>/<slug>.pdf` by default. Override with `--output <path>`. The same `.md-publisher/<timestamp>/` directory holds the backup of the original markdown when `preprocess` runs.

## First-run setup

The first time any skill runs, the plugin bootstraps a Python venv and Node toolchain at `~/.md-publisher/runtime/`. This takes ~2 minutes (downloads ~250 MB total: WeasyPrint, Pygments, mermaid-cli, and Puppeteer's Chromium). Subsequent runs reuse the cache.

**System dependency** that the bootstrap CANNOT install for you: a GTK 3 runtime (Pango / Cairo / GDK-Pixbuf), needed by WeasyPrint for native typography. On Windows the easiest source is Inkscape or GIMP (their bundled GTK works); on Linux `apt install libpango-1.0-0 libcairo2 libgdk-pixbuf2.0-0`; on macOS `brew install pango cairo gdk-pixbuf libffi`. The bootstrap probes for it and prints a clear error with install instructions if missing.

## Layout

```
md-publisher/
├── .claude-plugin/plugin.json
├── README.md
├── skills/
│   ├── publish/        SKILL.md + scripts/
│   ├── preprocess/     SKILL.md + scripts/
│   ├── theme-advisor/  SKILL.md + scripts/
│   └── theme-gallery/  SKILL.md + scripts/
├── lib/                shared Python pipeline
├── themes/             six built-in themes + theme-spec.json + pygments.css
└── runtime/            bootstrap + dep manifests
```

User-data lives at `~/.md-publisher/`:

```
~/.md-publisher/
├── runtime/.venv/      Python deps installed once at bootstrap
├── runtime/node_modules/    mermaid-cli + Chromium
└── themes/<slug>/      user-created themes (theme-advisor output)
```

## License

MIT — see [LICENSE](LICENSE).
