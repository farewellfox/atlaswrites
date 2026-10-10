# atlaswrites.com

The site builds itself. Push a change to GitHub and about a minute later it's live.

## Revising a story

1. Save the new version of the `.docx` into the story's folder in `content/`, replacing the old one. Easiest: keep your working copy there and edit it in place.
2. Optional: preview it (see below).
3. In VS Code, commit and push.

That's it. Check progress in the repo's **Actions** tab on GitHub. A green tick means it's live.

- The file name doesn't matter. If the folder holds a `.docx` and a `.md`, the `.docx` wins, so delete the old `.md` once you switch to Word.
- Close the document in Word before committing. Word's temporary `~$` files are ignored, so they won't get pushed.
- Word count, reading time, chapter list and the "Revised" date update themselves.
- Tracked changes are accepted and comments are left out, so there's no need to clean them up first.

## Adding a new piece

1. Make a folder in `content/fiction/`, `content/poetry/` or `content/comics/`. The folder name becomes the web address, so `content/fiction/night-train/` is published at `atlaswrites.com/fiction/night-train/`.
2. Put the `.docx` (or `.md`) inside.
3. Copy a `story.yml` from another story and edit it.

For a new story in **The Shrike**, make the folder inside `content/fiction/the-shrike/` and give its `story.yml` the next `number:`.

### story.yml

```
title: Night Train
genre: Spy fiction
status: Complete            # optional. Anything other than "Complete" is flagged on the home page
published: 2026-11          # year-month, or a full date like 2026-11-14
hook: One line for the home page.
blurb: |
  The longer description for the Fiction page and link previews.

  A blank line starts a new paragraph.
revised: 2026-12            # optional. Normally worked out from your uploads
royal_road: https://...     # optional
note: Shown in italics above the text   # optional
image: /assets/picture.jpg  # optional, with image_alt and image_caption
```

## How to format the Word file

- **Headings:** use Word's Heading styles. The biggest heading level you use becomes a part ("Part 1", "Scene 3") and the next level down becomes a chapter. Chapters that are just a number ("1", "Chapter 2") get the red numeral. Plain text doesn't need any styling.
- **Scene breaks:** type `***` or `#` on a line of its own, or use Word's horizontal line.
- **Title:** you can leave it at the top of the document. It's dropped automatically because the page already shows it.
- **Poems:** use Shift+Enter between lines in a stanza and Enter between stanzas.
- Italics, bold and footnotes all carry over.

## Previewing on your computer

Install pandoc once. On Windows, in a terminal:

```
winget install --id JohnMacFarlane.Pandoc
```

(Mac: `brew install pandoc`. Restart VS Code afterwards so it finds pandoc.)

Then, in VS Code's terminal (Terminal → New Terminal), from the repo folder:

```
python build.py --serve
```

Open http://localhost:8000 and press Ctrl+C to stop. Use `python3` instead of `python` if that's what your computer calls it. The preview goes into `_site/`, which git ignores, so it never gets pushed.

## What's where

| Path | What it is |
|---|---|
| `content/site.yml` | Site name, tagline, email, Royal Road link |
| `content/about.md` | About page text |
| `content/<section>/section.yml` | Section title and description |
| `assets/` | Stylesheet, script, ink bottle and other images |
| `build.py` | Turns `content/` into the finished site |
| `.github/workflows/deploy.yml` | Runs the build on GitHub and publishes it |
