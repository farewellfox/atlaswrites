#!/usr/bin/env python3
"""
Builds atlaswrites.com from the content/ folder into _site/.

    python3 build.py           build the site into _site/
    python3 build.py --serve   build, then preview at http://localhost:8000

Needs Python 3.8+ and pandoc (https://pandoc.org/installing.html). No other installs.

How content is laid out
-----------------------
content/site.yml                    site name, tagline, email, links
content/about.md                    the About page text
content/<section>/section.yml       one per section (fiction, poetry, comics)
content/<section>/<piece>/          one folder per story, poem or script:
    story.yml                       title, blurb, genre, date...
    <anything>.docx  or  .md        the text itself (a .docx wins if both exist)
content/<section>/<series>/series.yml   a series; its stories are subfolders

The folder path is the web address: content/fiction/the-shrike/provenance/
becomes atlaswrites.com/fiction/the-shrike/provenance/
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from email.utils import format_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONTENT = ROOT / "content"
ASSETS = ROOT / "assets"
OUT = ROOT / "_site"
WORDS_PER_MINUTE = 230
TEXT_EXTS = (".docx", ".md", ".markdown")
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")
NAV = [("Fiction", "/fiction/"), ("Poetry", "/poetry/"), ("Comics", "/comics/"),
       ("About", "/about/")]
READ_LABEL = {"fiction": "Read the story", "poetry": "Read the poem", "comics": "Read the script"}

warnings: list[str] = []


def warn(msg: str) -> None:
    warnings.append(msg)
    print(f"  warning: {msg}", file=sys.stderr)


def esc(value) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


# ─────────────────────────── reading .yml files ───────────────────────────
def read_meta(path: Path) -> dict:
    """Reads simple 'name: value' files. 'name: |' starts a multi-line value
    whose lines are indented underneath it. Lines starting with # are notes."""
    meta, key, block = {}, None, None
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        if block is not None:
            if raw.startswith((" ", "\t")) or not raw.strip():
                block.append(raw.strip())
                continue
            meta[key] = "\n".join(block).strip()
            block = None
        line = raw.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        k, v = line.split(":", 1)
        k, v = k.strip().lower(), v.strip()
        if v in ("|", ">"):
            key, block = k, []
            continue
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        meta[k] = v
    if block is not None:
        meta[key] = "\n".join(block).strip()
    return meta


def split_frontmatter(text: str) -> tuple[dict, str]:
    """Old-style .md files may start with a --- block; use it as a fallback."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    meta = {}
    for line in text[3:end].splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip().lower()] = v.strip()
    return meta, text[end + 4:].lstrip("\n")


def paragraphs(text: str) -> list[str]:
    return [p.strip().replace("\n", " ") for p in re.split(r"\n\s*\n", text or "") if p.strip()]


def parse_date(value: str | None, where: str) -> dt.date | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m", "%B %Y", "%b %Y", "%Y"):
        try:
            return dt.datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            pass
    warn(f"{where}: couldn't read the date '{value}'. Use 2026-07 or 2026-07-15.")
    return None


def month_year(d: dt.date | None) -> str:
    return f"{d.strftime('%B')} {d.year}" if d else ""


def git_date(path: Path) -> dt.date | None:
    """Date of the last commit that changed this file, used for 'Revised'.
    A file that has only been committed once (just added) has no revision yet."""
    try:
        out = subprocess.run(["git", "log", "--format=%cs", "--", str(path)], cwd=ROOT,
                             capture_output=True, text=True, timeout=15).stdout.split()
        return dt.date.fromisoformat(out[0]) if len(out) > 1 else None
    except Exception:
        return None


# ─────────────────────────── converting the text ───────────────────────────
HEAD_RE = re.compile(r"<h([1-6])([^>]*)>(.*?)</h\1>", re.S)
NUMERIC = re.compile(r"^\s*(?:chapter\s+)?([0-9]+|[ivxlcdm]+)\.?\s*$", re.I)
BREAK_HTML = '<hr class="break">'


def plain(fragment: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", fragment)).strip()


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def find_source(folder: Path) -> Path | None:
    files = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in TEXT_EXTS
             and not p.name.startswith(("~$", "."))]
    files.sort(key=lambda p: (p.suffix.lower() != ".docx", p.name))
    return files[0] if files else None


def word_rules_to_breaks(src: Path, tmp: Path) -> Path:
    """pandoc ignores Word's horizontal lines (Insert > Horizontal Line, or typing ---
    then Enter). Writers use them as scene breaks, so swap each empty ruled paragraph
    for a *** paragraph, which becomes a scene break later."""
    with zipfile.ZipFile(src) as z:
        xml = z.read("word/document.xml").decode("utf-8")

    def fix(m: re.Match) -> str:
        p = m.group(0)
        ruled = 'o:hr="t"' in p or "<w:pBdr" in p
        has_text = re.search(r"<w:t(?:\s[^>]*)?>[^<]*\S", p)
        return "<w:p><w:r><w:t>***</w:t></w:r></w:p>" if ruled and not has_text else p

    new = re.sub(r"<w:p\b(?:(?!<w:p\b).)*?</w:p>", fix, xml, flags=re.S)
    if new == xml:
        return src
    out = tmp / src.name
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = new.encode("utf-8") if item.filename == "word/document.xml" else zin.read(item)
            zout.writestr(item, data)
    return out


def pandoc(src: Path, out_dir: Path) -> tuple[str, dict]:
    """Word or Markdown in, an HTML fragment out. Images inside a Word file
    are saved next to the page."""
    if src.suffix.lower() == ".docx":
        with tempfile.TemporaryDirectory() as tmp:
            docx = word_rules_to_breaks(src, Path(tmp))
            cmd = ["pandoc", str(docx), "--from=docx", "--to=html5", "--wrap=none",
                   "--extract-media=."]
            res = subprocess.run(cmd, cwd=out_dir, capture_output=True, text=True)
        front = {}
    else:
        front, text = split_frontmatter(src.read_text(encoding="utf-8-sig"))
        cmd = ["pandoc", "--from=markdown", "--to=html5", "--wrap=none"]
        res = subprocess.run(cmd, input=text, cwd=out_dir, capture_output=True, text=True)
    if res.returncode != 0:
        sys.exit(f"\npandoc couldn't convert {src.relative_to(ROOT)}:\n{res.stderr}")
    return res.stdout, front


def shape_text(body: str, title: str) -> tuple[str, list]:
    """Turns headings into parts and chapters, styles scene breaks, builds the contents.

    Whatever heading levels the document uses, the biggest becomes a 'part'
    (Part 1, Scene 3) and the next one down a 'chapter' (1, 2, 3 or Panel 4).
    A document with one level of headings: numbers become chapters, words become parts."""
    body = body.strip()
    first = HEAD_RE.match(body)
    if first and norm(plain(first.group(3))) == norm(title):
        body = body[first.end():].lstrip()            # don't repeat the title

    body = re.sub(r"<hr\s*/?>", BREAK_HTML, body)
    body = re.sub(r"<p>\s*(?:[*#~⁂•◆]\s*){1,5}</p>", BREAK_HTML, body)  # *** or # on its own line

    heads = list(HEAD_RE.finditer(body))
    levels = sorted({int(h.group(1)) for h in heads})
    role = {}
    if len(levels) >= 2:
        role = {levels[0]: "part", levels[1]: "chapter"}
    elif levels:
        numeric = all(NUMERIC.match(plain(h.group(3))) for h in heads)
        role = {levels[0]: "chapter" if numeric else "part"}

    toc: list[dict] = []
    count = {"part": 0, "chapter": 0}

    def replace(h: re.Match) -> str:
        inner = h.group(3).strip()
        text = plain(inner)
        kind = role.get(int(h.group(1)), "minor")
        if kind == "part":
            count["part"] += 1
            count["chapter"] = 0
            hid = f"part-{count['part']}"
            toc.append({"id": hid, "label": text, "chapters": []})
            return f'<h2 class="part" id="{hid}">{inner}</h2>'
        if kind == "chapter":
            count["chapter"] += 1
            hid = (f"part-{count['part']}-" if count["part"] else "") + f"chapter-{count['chapter']}"
            num = NUMERIC.match(text)
            entry = {"id": hid, "label": num.group(1) if num else text, "numeric": bool(num)}
            if count["part"] and toc:
                toc[-1]["chapters"].append(entry)
            else:
                toc.append({"id": hid, "label": f"Chapter {entry['label']}" if num else text,
                            "chapters": []})
            if num:
                return (f'<h3 class="chapter chapter--num" id="{hid}">'
                        f'<span class="visually-hidden">Chapter </span>'
                        f'<span class="num">{esc(num.group(1))}</span></h3>')
            return f'<h3 class="chapter" id="{hid}">{inner}</h3>'
        return f'<h4 class="minor">{inner}</h4>'

    return HEAD_RE.sub(replace, body), toc


ELISIONS = {"em", "tis", "twas", "bout", "cause", "til", "till", "round", "n", "is", "im",
            "e", "er", "ere", "ow", "ello", "s", "ave", "ad", "un", "nother", "fraid"}


def smart_quotes(text: str) -> str:
    """Curls straight quotes in plain text: "word" and it's become “word” and it’s."""
    out, prev = [], " "
    for i, ch in enumerate(text):
        nxt = text[i + 1] if i + 1 < len(text) else " "
        opener = prev.isspace() or prev in "([{—–-/‘“"
        if ch == '"':
            ch = "“" if opener else "”"
        elif ch == "'":
            if opener and nxt.isalpha():
                word = re.match(r"[A-Za-z]+", text[i + 1:]).group(0).lower()
                ch = "’" if word in ELISIONS else "‘"
            elif opener and nxt.isdigit():
                ch = "’"                                 # '90s
            else:
                ch = "’"
        out.append(ch)
        prev = ch
    return "".join(out)


def smart_html(body: str) -> str:
    """Applies smart_quotes to the text of an HTML fragment, leaving tags alone."""
    out, carry = [], " "
    for part in re.split(r"(<[^>]+>)", body):
        if part.startswith("<"):
            if re.match(r"</?(p|h[1-6]|li|br|blockquote|div|hr|td|th|figcaption)\b", part, re.I):
                carry = " "
            out.append(part)
            continue
        raw = html.unescape(part)
        fixed = smart_quotes(carry + raw)[1:]
        if raw:
            carry = raw[-1]
        out.append(html.escape(fixed, quote=False))
    return "".join(out)


def count_words(body: str) -> int:
    return sum(1 for w in plain(body).split() if re.search(r"\w", w))


def reading_time(words: int) -> str:
    minutes = max(1, round(words / WORDS_PER_MINUTE))
    if minutes < 60:
        return f"{minutes} min"
    return f"{minutes // 60} h {minutes % 60} min" if minutes % 60 else f"{minutes // 60} h"


# ─────────────────────────── loading the content ───────────────────────────
def load_story(folder: Path, section: dict, url: str, series: dict | None) -> dict | None:
    where = str(folder.relative_to(ROOT))
    meta = read_meta(folder / "story.yml")
    src = find_source(folder)
    if not src:
        warn(f"{where}: no .docx or .md file found, so it was skipped.")
        return None
    out_dir = OUT / url.strip("/")
    out_dir.mkdir(parents=True, exist_ok=True)
    raw, front = pandoc(src, out_dir)
    for k, v in front.items():                     # old front matter fills any gaps
        meta.setdefault({"description": "blurb", "meta": "_meta"}.get(k, k), v)
    if not meta.get("title"):
        warn(f"{where}: story.yml has no title.")
        meta["title"] = folder.name.replace("-", " ").title()
    for field in ("title", "hook", "blurb", "note", "image_caption"):
        if meta.get(field):
            meta[field] = smart_quotes(meta[field])
    body, toc = shape_text(smart_html(raw), meta["title"])
    for f in folder.iterdir():                     # images that sit next to the text
        if f.suffix.lower() in IMAGE_EXTS:
            shutil.copy2(f, out_dir / f.name)

    words = count_words(body)
    published = parse_date(meta.get("published"), where)
    revised = parse_date(meta.get("revised"), where) or git_date(src)
    kind = section["slug"]
    story = {
        **meta, "slug": folder.name, "url": url, "kind": kind, "section": section,
        "series": series, "body": body, "toc": toc, "words": words,
        "time": reading_time(words), "published": published, "revised": revised,
        "source": src, "blurb_paras": paragraphs(meta.get("blurb", "")),
        "genres": meta.get("genre", ""), "author": meta.get("author") or SITE.get("author", ""),
    }
    if kind == "poetry":
        lines = body.count("<br") + body.count("<p")
        story["length"] = f"{lines} lines"
    elif kind == "comics":
        scenes = sum(1 for t in toc if t["label"].lower().startswith("scene"))
        story["length"] = f"{scenes} scenes" if scenes else story["time"]
    else:
        story["length"] = story["time"]
    print(f"  {url:<45} {words:>7,} words  from {src.name}")
    return story


def load_series(folder: Path, section: dict) -> dict:
    meta = read_meta(folder / "series.yml")
    for field in ("title", "hook", "blurb"):
        if meta.get(field):
            meta[field] = smart_quotes(meta[field])
    url = f"/{section['slug']}/{folder.name}/"
    series = {**meta, "slug": folder.name, "url": url, "kind": "series", "section": section,
              "blurb_paras": paragraphs(meta.get("blurb", "")), "stories": []}
    for sub in sorted(p for p in folder.iterdir() if p.is_dir()):
        if (sub / "story.yml").exists():
            s = load_story(sub, section, f"{url}{sub.name}/", series)
            if s:
                series["stories"].append(s)
    series["stories"].sort(key=lambda s: (int(s.get("number") or 999), s["published"] or dt.date.max))
    dates = [s["published"] for s in series["stories"] if s["published"]]
    series["published"] = max(dates) if dates else None
    return series


def load_sections() -> list[dict]:
    sections = []
    for folder in sorted(p for p in CONTENT.iterdir() if p.is_dir()):
        if not (folder / "section.yml").exists():
            continue
        sec = {**read_meta(folder / "section.yml"), "slug": folder.name, "url": f"/{folder.name}/"}
        items = []
        for sub in sorted(p for p in folder.iterdir() if p.is_dir()):
            if (sub / "series.yml").exists():
                items.append(load_series(sub, sec))
            elif (sub / "story.yml").exists():
                s = load_story(sub, sec, f"/{folder.name}/{sub.name}/", None)
                if s:
                    items.append(s)
        items.sort(key=lambda i: i["published"] or dt.date.min, reverse=True)
        sec["items"] = items
        sections.append(sec)
    sections.sort(key=lambda s: int(s.get("order") or 99))
    return sections


def all_stories(sections: list[dict]) -> list[dict]:
    out = []
    for sec in sections:
        for item in sec["items"]:
            out.extend(item["stories"] if item["kind"] == "series" else [item])
    return out


# ─────────────────────────── page templates ───────────────────────────
SITE: dict = {}

ICON_THEME = ('<svg class="ico-moon" viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" focusable="false">'
              '<path d="M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5Z" fill="none" '
              'stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></svg>'
              '<svg class="ico-sun" viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" focusable="false">'
              '<g fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><circle cx="12" cy="12" r="4"/>'
              '<path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4"/></g></svg>')


def layout(*, title: str, desc: str, path: str, main: str, current: str = "",
           og_type: str = "website", image: str | None = None, home: bool = False,
           body_class: str = "", footer_links: bool = True) -> str:
    base = SITE["url"].rstrip("/")
    full_title = SITE["title"] if home else f"{title} – {SITE['title']}"
    img = image or "/assets/inkbottle.png"
    img_abs = img if img.startswith("http") else base + img
    here = ' aria-current="page"'
    nav = "".join(f'<a href="{u}"{here if u == current else ""}>{n}</a>' for n, u in NAV)
    brand_size = " masthead--home" if home else ""
    tagline = f'<p class="tagline">{esc(SITE.get("tagline"))}</p>' if home else ""
    links = (f'<a href="mailto:{esc(SITE.get("email"))}">Email</a><a href="{esc(SITE.get("royal_road"))}" rel="noopener">Royal Road</a>'
             f'<a href="/feed.xml">RSS feed</a>') if footer_links else f'<span class="footer-note">© {dt.date.today().year} {esc(SITE["title"])}</span>'
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(full_title)}</title>
<meta name="description" content="{esc(desc)}">
<link rel="canonical" href="{base}{path}">
<meta property="og:title" content="{esc(title if not home else SITE['title'])}">
<meta property="og:description" content="{esc(desc)}">
<meta property="og:url" content="{base}{path}">
<meta property="og:type" content="{og_type}">
<meta property="og:image" content="{esc(img_abs)}">
<meta name="twitter:card" content="summary">
<link rel="icon" href="/assets/inkbottle.png">
<link rel="alternate" type="application/rss+xml" title="{esc(SITE['title'])}" href="/feed.xml">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Literata:ital,opsz,wght@0,7..72,400..700;1,7..72,400..700&display=swap">
<link rel="stylesheet" href="/assets/style.css">
<meta name="theme-color" content="#F6F6F2" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#131922" media="(prefers-color-scheme: dark)">
<script>try{{var t=localStorage.getItem('theme');if(t)document.documentElement.dataset.theme=t;var s=localStorage.getItem('scale');if(s)document.documentElement.style.setProperty('--scale',s);}}catch(e){{}}</script>
</head>
<body class="{body_class}">
<a class="skip" href="#main">Skip to content</a>
<div class="progress" aria-hidden="true"><span></span></div>
<div class="frame">
<header class="row masthead{brand_size}">
  <div class="m"><a class="brand brand-a" href="/" aria-label="atlas writes, home">atlas</a></div>
  <div class="t">
    <div class="brand-line"><a class="brand brand-w" href="/" tabindex="-1" aria-hidden="true">writes</a>{tagline}</div>
    <nav class="nav" aria-label="Main">{nav}<button class="theme-toggle" type="button" aria-pressed="false" aria-label="Switch to dark theme">{ICON_THEME}<span class="tt-label">Dark</span></button></nav>
  </div>
</header>
<main id="main">
{main}
</main>
<footer class="row footer">
  <div class="m"><img class="mark" src="/assets/inkbottle.png" alt="" width="28" height="28"></div>
  <div class="t">{links}</div>
</footer>
<div class="row fill" aria-hidden="true"><div class="m"></div><div class="t"></div></div>
</div>
<script src="/assets/site.js" defer></script>
</body>
</html>
"""


def row(margin: str, text: str, cls: str = "") -> str:
    return f'<div class="row {cls}"><div class="m">{margin}</div><div class="t">{text}</div></div>'


def facts_line(item: dict) -> str:
    bits = [item.get("genres") or item.get("genre"), item.get("status")]
    if item["kind"] == "series":
        n = len(item["stories"])
        bits.append(f"{n} {'story' if n == 1 else 'stories'}")
    else:
        if item["kind"] != "poetry":
            bits.append(f"{item['words']:,} words")
        bits.append(month_year(item["published"]))
    return "".join(f"<span>{esc(b)}</span>" for b in bits if b)


def toc_entry(item: dict) -> str:
    """One item on the home page: the title, and one quiet line underneath."""
    def meta(bits):
        return '<p class="toc-meta">' + "".join(bits) + "</p>"
    status = item.get("status") or ""
    flag = (f'<span class="toc-flag">{esc(status)}</span>'
            if status and status.lower() not in ("complete", "ongoing series") else "")
    if item["kind"] == "series":
        kids = ""
        for s in item["stories"]:
            length = meta([f"<span>{esc(s['length'])}</span>"])
            kids += (f'<li class="toc-item toc-item--kid"><a class="toc-title" href="{s["url"]}">'
                     f'{esc(s["title"])}</a>{length}</li>')
        genre = item.get("genres") or item.get("genre")
        bits = [f"<span>{esc(genre)}</span>" if genre else "",
                f"<span>{esc(status or 'Series')}</span>", flag]
        return (f'<li class="toc-item"><a class="toc-title" href="{item["url"]}">{esc(item["title"])}</a>'
                f'{meta(bits)}<ol class="toc-kids">{kids}</ol></li>')
    bits = [f"<span>{esc(item.get('genres'))}</span>" if item.get("genres") else "",
            f"<span>{esc(item['length'])}</span>", flag]
    return f'<li class="toc-item"><a class="toc-title" href="{item["url"]}">{esc(item["title"])}</a>{meta(bits)}</li>'


def page_home(sections: list[dict]) -> str:
    rows = []
    for sec in sections:
        if not sec["items"]:
            continue
        entries = "".join(toc_entry(i) for i in sec["items"])
        rows.append(row(f'<h2 class="side-label"><a href="{sec["url"]}">{esc(sec["title"])}</a></h2>',
                        f'<ol class="toc-list">{entries}</ol>', "contents-row"))
    main = "\n".join(rows)
    return layout(title=SITE["title"], desc=SITE.get("description", ""), path="/", main=main,
                  home=True, body_class="home")


def entry_card(item: dict, heading: str = "h2") -> str:
    label = "See the series" if item["kind"] == "series" else READ_LABEL.get(item["kind"], "Read")
    blurb = "".join(f"<p>{esc(p)}</p>" for p in item["blurb_paras"][:2 if item["kind"] == "series" else 9])
    kids = ""
    if item["kind"] == "series" and item["stories"]:
        kids = "<ol class='entry-kids'>" + "".join(
            f'<li><a href="{s["url"]}">{esc(s["title"])}</a> <span class="muted">{esc(s["length"])}</span></li>'
            for s in item["stories"]) + "</ol>"
    return (f'<article class="entry"><{heading} class="entry-title"><a href="{item["url"]}">{esc(item["title"])}</a></{heading}>'
            f'<p class="facts-line">{facts_line(item)}</p><div class="entry-blurb">{blurb}</div>{kids}'
            f'<a class="entry-link" href="{item["url"]}">{label}</a></article>')


def page_section(sec: dict) -> str:
    entries = "".join(entry_card(i) for i in sec["items"]) or '<p class="muted">Nothing here yet.</p>'
    main = row(f'<h1 class="side-label side-label--page">{esc(sec["title"])}</h1>', entries, "page-row")
    return layout(title=sec["title"], desc=sec.get("description", ""), path=sec["url"], main=main,
                  current=sec["url"])


def page_series(series: dict) -> str:
    sec = series["section"]
    blurb = "".join(f"<p>{esc(p)}</p>" for p in series["blurb_paras"])
    entries = "".join(entry_card(s, "h3") for s in series["stories"])
    side = (f'<a class="back" href="{sec["url"]}">All {esc(sec["title"].lower())}</a>'
            f'<p class="side-facts">{esc(series.get("status"))}</p>')
    text = (f'<h1 class="story-title">{esc(series["title"])}</h1><div class="series-blurb">{blurb}</div>'
            f'<h2 class="list-heading">Stories in the series</h2>{entries}')
    return layout(title=series["title"], desc=(series["blurb_paras"] or [""])[0], path=series["url"],
                  main=row(side, text, "page-row"), current=sec["url"])


def toc_html(toc: list) -> str:
    if len(toc) < 2:
        return ""
    items = []
    for t in toc:
        chapters = t["chapters"]
        items.append(f'<li><a href="#{t["id"]}">{esc(t["label"])}</a></li>')
    return (f'<details class="toc"><summary>Contents</summary><ol class="toc-parts">{"".join(items)}</ol></details>')


def page_story(s: dict) -> str:
    sec, series = s["section"], s["series"]
    back = (f'<a class="back" href="{series["url"]}">{esc(series["title"])}</a>' if series
            else f'<a class="back" href="{sec["url"]}">All {esc(sec["title"].lower())}</a>')
    facts = [s.get("genres")]
    if s["kind"] != "poetry":
        facts += [f"{s['words']:,} words", f"About {s['time']}"]
    facts.append(month_year(s["published"]))
    if s["revised"] and s["published"] and (s["revised"].year, s["revised"].month) > (s["published"].year, s["published"].month):
        facts.append(f"Revised {month_year(s['revised'])}")
    if s.get("status") and s["status"].lower() != "complete":
        facts.append(s["status"])
    facts_html = "".join(f"<li>{esc(f)}</li>" for f in facts if f)
    side = f'<div class="side-sticky">{back}<ul class="side-facts">{facts_html}</ul>{toc_html(s["toc"])}</div>'

    figure = ""
    if s.get("image"):
        cap = f'<figcaption>{esc(s["image_caption"])}</figcaption>' if s.get("image_caption") else ""
        figure = f'<figure class="story-figure"><img src="{esc(s["image"])}" alt="{esc(s.get("image_alt", ""))}">{cap}</figure>'
    note = f'<p class="story-note">{esc(s["note"])}</p>' if s.get("note") else ""
    by = f'<p class="byline">by {esc(s["author"])}</p>' if s.get("author") else ""

    end = ['<div class="story-end">']
    if series:
        sibs = series["stories"]
        i = sibs.index(s)
        if i + 1 < len(sibs):
            end.append(f'<p>Next in {esc(series["title"])}: <a href="{sibs[i + 1]["url"]}">{esc(sibs[i + 1]["title"])}</a></p>')
        else:
            end.append(f'<p>That\'s the latest in {esc(series["title"])}. <a href="{series["url"]}">See the series</a></p>')
    if s.get("royal_road"):
        end.append(f'<p>Also on <a href="{esc(s["royal_road"])}" rel="noopener">Royal Road</a></p>')
    end.append(f'<p><a href="{sec["url"]}">More {esc(sec["title"].lower())}</a></p></div>')

    text = (f'<header class="story-head"><h1 class="story-title">{esc(s["title"])}</h1>{by}</header>'
            f'<div class="resume" hidden><p>Pick up where you left off<span class="resume-where"></span>.</p>'
            f'<button type="button" class="resume-go">Continue reading</button>'
            f'<button type="button" class="resume-x" aria-label="Dismiss">×</button></div>'
            f'{figure}{note}<div class="prose prose--{s["kind"]}">{s["body"]}</div>{"".join(end)}')
    main = f'<article class="row story" data-story="{esc(s["url"])}"><aside class="m">{side}</aside><div class="t">{text}</div></article>'
    desc = (s["blurb_paras"] or [s.get("hook", "")])[0]
    return layout(title=s["title"], desc=desc, path=s["url"], main=main, current=sec["url"],
                  og_type="article", image=s.get("image"), body_class="reading")


def page_about() -> str:
    src = CONTENT / "about.md"
    body = ""
    if src.exists():
        res = subprocess.run(["pandoc", "--from=markdown", "--to=html5", "--wrap=none"],
                             input=split_frontmatter(src.read_text(encoding="utf-8"))[1],
                             capture_output=True, text=True)
        body = smart_html(res.stdout)
    email, rr = esc(SITE.get("email")), esc(SITE.get("royal_road"))
    contact = (f'<ul class="toc-list contact-list">'
               f'<li class="toc-item toc-item--static"><p class="contact-lead">Questions or notes on a story? An artist interested in <a href="/comics/undercity-crown-issue-1/">Undercity Crown</a>? Email me.</p>'
               f'<p class="contact-email"><a href="mailto:{email}">{email}</a>'
               f'<button type="button" class="copy-btn" data-copy="{email}">Copy address</button></p></li>'
               f'<li class="toc-item"><a class="toc-title" href="{rr}" rel="noopener">Royal Road</a><span class="toc-go" aria-hidden="true">↗</span>'
               f'<p class="toc-meta"><span>More of my fiction</span></p></li>'
               f'<li class="toc-item"><a class="toc-title" href="/feed.xml">RSS feed</a><span class="toc-go" aria-hidden="true">→</span>'
               f'<p class="toc-meta"><span>New stories as they go up</span></p></li></ul>')
    main = (row('<h1 class="side-label">About</h1>', f'<div class="entry about-text">{body}</div>', "contents-row")
            + row('<h2 class="side-label" id="contact">Get in touch</h2>', contact, "contents-row"))
    return layout(title="About", desc="About Atlas, who writes noir, spy fiction, fantasy and comics. Get in touch by email.",
                  path="/about/", main=main, current="/about/", footer_links=False)


def page_contact() -> str:
    """The old /contact/ address now forwards to the About page."""
    return ('<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>Contact</title>'
            '<link rel="canonical" href="/about/#contact"><meta http-equiv="refresh" content="0; url=/about/#contact">'
            '</head><body><p><a href="/about/#contact">Contact details are on the About page.</a></p></body></html>')


def page_404() -> str:
    text = ('<h1 class="story-title">This page doesn\'t exist</h1>'
            '<p>The link may be old, or the story may have moved. Everything on the site is listed on the <a href="/">home page</a>.</p>')
    return layout(title="Page not found", desc="Page not found", path="/404.html",
                  main=row("", text, "page-row"))


def feed_xml(stories: list[dict]) -> str:
    base = SITE["url"].rstrip("/")
    items = []
    for s in sorted(stories, key=lambda s: s["published"] or dt.date.min, reverse=True)[:30]:
        d = s["published"] or dt.date.today()
        when = format_datetime(dt.datetime(d.year, d.month, d.day, 12, tzinfo=dt.timezone.utc))
        items.append(f"<item><title>{esc(s['title'])}</title><link>{base}{s['url']}</link>"
                     f"<guid>{base}{s['url']}</guid><pubDate>{when}</pubDate>"
                     f"<description>{esc((s['blurb_paras'] or [''])[0])}</description></item>")
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0"><channel>'
            f"<title>{esc(SITE['title'])}</title><link>{base}/</link>"
            f"<description>{esc(SITE.get('description'))}</description>{''.join(items)}</channel></rss>\n")


def sitemap_xml(urls: list[tuple[str, dt.date | None]]) -> str:
    base = SITE["url"].rstrip("/")
    rows = "".join(f"<url><loc>{base}{u}</loc>{f'<lastmod>{d.isoformat()}</lastmod>' if d else ''}</url>"
                   for u, d in urls)
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{rows}</urlset>\n'


# ─────────────────────────── build ───────────────────────────
def write(url_path: str, content: str) -> None:
    target = OUT / url_path.strip("/") / "index.html" if url_path.endswith("/") else OUT / url_path.lstrip("/")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def build() -> None:
    if shutil.which("pandoc") is None:
        sys.exit("pandoc isn't installed. Get it from https://pandoc.org/installing.html")
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()
    SITE.update(read_meta(CONTENT / "site.yml"))
    print("Building atlaswrites")
    sections = load_sections()
    stories = all_stories(sections)

    urls: list[tuple[str, dt.date | None]] = [("/", None)]
    write("/", page_home(sections))
    for sec in sections:
        write(sec["url"], page_section(sec))
        urls.append((sec["url"], None))
        for item in sec["items"]:
            if item["kind"] == "series":
                write(item["url"], page_series(item))
                urls.append((item["url"], None))
    for s in stories:
        write(s["url"], page_story(s))
        urls.append((s["url"], s["revised"] or s["published"]))
    write("/about/", page_about())
    write("/contact/", page_contact())
    urls.append(("/about/", None))
    write("/404.html", page_404())
    write("/feed.xml", feed_xml(stories))
    write("/sitemap.xml", sitemap_xml(urls))
    write("/robots.txt", f"User-agent: *\nAllow: /\nSitemap: {SITE['url'].rstrip('/')}/sitemap.xml\n")
    if ASSETS.exists():
        shutil.copytree(ASSETS, OUT / "assets")
    for extra in ("CNAME", "favicon.ico"):
        if (ROOT / extra).exists():
            shutil.copy2(ROOT / extra, OUT / extra)
    (OUT / ".nojekyll").write_text("")
    print(f"Done: {len(stories)} pieces, {len(urls)} pages in _site/"
          + (f", {len(warnings)} warning(s)" if warnings else ""))


def serve(port: int = 8000) -> None:
    import functools
    import http.server
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(OUT))
    print(f"Preview: http://localhost:{port}  (Ctrl+C to stop)")
    http.server.ThreadingHTTPServer(("", port), handler).serve_forever()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Build atlaswrites.com")
    ap.add_argument("--serve", action="store_true", help="preview the site after building")
    args = ap.parse_args()
    build()
    if args.serve:
        serve()
