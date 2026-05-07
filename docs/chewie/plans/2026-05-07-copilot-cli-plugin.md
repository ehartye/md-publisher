# Copilot CLI Plugin Support — Implementation Plan

> **Execution:** Use @chewie:hyperdrive (parallel team) or @chewie:execute-plan (sequential solo) to execute this plan.

**Goal:** Add GitHub Copilot CLI plugin discoverability to md-publisher without changing any existing files.

**Architecture:** Add `.github/plugin/marketplace.json` for marketplace discovery, `.github/copilot-instructions.md` for plugin context, and update README.md with dual install instructions. Copilot CLI finds the existing `.claude-plugin/plugin.json` via its fallback search order.

**Tech Stack:** JSON (marketplace manifest), Markdown (instructions + README)

---

### Task 1: Create marketplace manifest

**Files:**
- Create: `.github/plugin/marketplace.json`

**Step 1: Create the directory structure**

```bash
mkdir -p .github/plugin
```

**Step 2: Write the marketplace manifest**

Create `.github/plugin/marketplace.json`:

```json
{
  "name": "md-publisher",
  "owner": {
    "name": "Eric Hartye",
    "email": "eric@hartye.com"
  },
  "metadata": {
    "description": "Markdown to themed PDF/DOCX publishing pipeline with mermaid support"
  },
  "plugins": [
    {
      "name": "md-publisher",
      "source": "./",
      "description": "Turn markdown documents (with embedded mermaid) into themed, searchable, paged PDFs and DOCX files via WeasyPrint and python-docx"
    }
  ]
}
```

**Step 3: Validate JSON is parseable**

Run: `python3 -c "import json; json.load(open('.github/plugin/marketplace.json'))"`
Expected: No output (success)

**Step 4: Commit**

```bash
git add .github/plugin/marketplace.json
git commit -m "feat: add Copilot CLI marketplace manifest"
```

---

### Task 2: Create copilot-instructions.md

**Files:**
- Create: `.github/copilot-instructions.md`

**Step 1: Write the instructions file**

Create `.github/copilot-instructions.md`:

```markdown
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
| `themes/` | 6 bundled themes (atlas/phosphor/arcade × light/dark) + default |
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
```

**Step 2: Commit**

```bash
git add .github/copilot-instructions.md
git commit -m "feat: add copilot-instructions.md for Copilot CLI context"
```

---

### Task 3: Update README with dual install instructions

**Files:**
- Modify: `README.md` (insert after the layout section, before "Verifying the install")

**Step 1: Add Installation section to README**

Insert the following after line 74 (end of user-data layout block) and before "## Verifying the install" (line 76):

```markdown
## Installation

### Claude Code

```bash
claude plugin install ehartye/md-publisher
```

### GitHub Copilot CLI

```bash
copilot plugin install ehartye/md-publisher
```

Both paths use the same skills and runtime — no difference in capability.

```

**Step 2: Update the opening line**

Change line 3 of README.md from:
```
A Claude Code plugin that turns markdown documents...
```
to:
```
A plugin for Claude Code and GitHub Copilot CLI that turns markdown documents...
```

**Step 3: Verify no tests are broken**

Run: `cd /Users/H468632/Documents/repos/poc-repos/md-publisher && python3 -m pytest tests/ -x -q 2>&1 | tail -5`
Expected: All tests pass (these are pipeline tests, not affected by docs changes)

**Step 4: Commit**

```bash
git add README.md
git commit -m "docs(README): add Copilot CLI install instructions + dual-plugin framing"
```

---

### Task 4: Validate Copilot CLI discovery

**Step 1: Test local plugin install**

Run: `copilot plugin install /Users/H468632/Documents/repos/poc-repos/md-publisher`
Expected: Successful install, plugin appears in list

**Step 2: Verify skills are loaded**

Run: `copilot plugin list`
Expected: `md-publisher` appears with 5 skills

**Step 3: Uninstall test plugin**

Run: `copilot plugin uninstall md-publisher`
Expected: Clean removal

**Step 4: Final commit (spec + plan docs)**

```bash
git add docs/
git commit -m "docs: add implementation plan for Copilot CLI plugin support"
```
