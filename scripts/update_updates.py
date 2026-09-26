#!/usr/bin/env python3
"""Refresh the "Latest updates" list and version tags on the homepage.

Reads each project's CHANGELOG.md through the GitHub API, keeps the newest
entries in updates.json, and re-renders the marked regions of index.html.

Auth: PROJECTS_READ_TOKEN (needed for private repos), else GITHUB_TOKEN / GH_TOKEN.
If a project cannot be read, its entries in updates.json are left as they were.
"""
import datetime
import html
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX = os.path.join(ROOT, "index.html")
STORE = os.path.join(ROOT, "updates.json")

ENTRIES_PER_PROJECT = 2
MAX_SUMMARY = 340

PROJECTS = [
    {"key": "cultivation", "repo": "EmanChan050528/cultivation-idle", "name": "Cultivation Idle", "color": "gold"},
    {"key": "jp-subs", "repo": "EmanChan050528/jp-subs", "name": "JP Subs", "color": "sky"},
    {"key": "whisper-subs", "repo": "EmanChan050528/whisper-subs", "name": "Whisper Subtitler", "color": "brand"},
]

HEADING = re.compile(r"^##\s+v?(\d+\.\d+\.\d+)\s*[—–-]\s*(.+?)\s*$")


def token():
    return (os.environ.get("PROJECTS_READ_TOKEN")
            or os.environ.get("GITHUB_TOKEN")
            or os.environ.get("GH_TOKEN"))


def api(path, raw=False):
    headers = {"User-Agent": "homepage-updates", "X-GitHub-Api-Version": "2022-11-28",
               "Accept": "application/vnd.github.raw+json" if raw else "application/vnd.github+json"}
    tok = token()
    if tok:
        headers["Authorization"] = "Bearer " + tok
    req = urllib.request.Request("https://api.github.com" + path, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode("utf-8")
    return body if raw else json.loads(body)


def clean(text):
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)      # links -> label
    text = re.sub(r"\*\*|__", "", text)                       # bold
    text = re.sub(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])", r"\1", text)  # emphasis
    return re.sub(r"\s+", " ", text).strip()


def summarize(lines):
    """First prose paragraph (or first bullet) under a heading, trimmed to whole sentences."""
    para, started = [], False
    for line in lines:
        s = line.strip()
        if not s:
            if started:
                break
            continue
        if s.startswith(("#", ">", "|", "```", "---")):
            if started:
                break
            continue
        if s.startswith(("- ", "* ")) and not started:
            para, started = [s[2:]], True
            continue
        if s.startswith(("- ", "* ")):
            break
        para.append(s)
        started = True
    text = clean(" ".join(para))
    if len(text) <= MAX_SUMMARY:
        return text
    out = ""
    for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", text):
        if out and len(out) + 1 + len(sentence) > MAX_SUMMARY:
            break
        out = (out + " " + sentence).strip()
    if len(out) > MAX_SUMMARY:
        out = out[:MAX_SUMMARY].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return out


def parse_changelog(md):
    entries, current = [], None
    for line in md.splitlines():
        m = HEADING.match(line)
        if m:
            current = {"version": m.group(1), "title": clean(m.group(2)), "body": []}
            entries.append(current)
        elif current is not None:
            current["body"].append(line)
    out = []
    for e in entries[:ENTRIES_PER_PROJECT]:
        title = e["title"]
        out.append({"version": e["version"],
                    "title": title[:1].upper() + title[1:],
                    "summary": summarize(e["body"])})
    return out


def tag_date(repo, version):
    for ref in ("v" + version, version):
        try:
            data = api("/repos/%s/commits/%s" % (repo, urllib.parse.quote(ref)))
            return data["commit"]["committer"]["date"][:10]
        except (urllib.error.URLError, KeyError, ValueError):
            continue
    return None


def load_store():
    try:
        with open(STORE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def fetch_project(project, previous):
    repo = project["repo"]
    try:
        md = api("/repos/%s/contents/CHANGELOG.md" % repo, raw=True)
    except (urllib.error.URLError, ValueError) as err:
        print("warning: could not read %s (%s); keeping existing entries" % (repo, err), file=sys.stderr)
        return previous
    known = {e["version"]: e.get("date") for e in previous}
    entries = parse_changelog(md)
    if not entries:
        print("warning: no versioned entries found in %s; keeping existing entries" % repo, file=sys.stderr)
        return previous
    for e in entries:
        e["date"] = known.get(e["version"]) or tag_date(repo, e["version"])
    return entries


def pretty_date(iso):
    if not iso:
        return ""
    d = datetime.date.fromisoformat(iso)
    return "%s %d, %d" % (d.strftime("%b"), d.day, d.year)


def inline(text):
    return re.sub(r"`([^`]+)`", r"<code>\1</code>", html.escape(text, quote=False))


def render_updates(store):
    rows = []
    for p in PROJECTS:
        for e in store.get(p["key"], []):
            rows.append((e.get("date") or "", p, e))
    rows.sort(key=lambda r: r[0], reverse=True)
    blocks = []
    for _, p, e in rows:
        date = pretty_date(e.get("date"))
        date_html = '<span class="date">%s</span>' % date if date else ""
        blocks.append(
            '        <div class="update">\n'
            '          <div class="what"><span class="ver" style="--c:var(--%s)">v%s</span>'
            '<span class="proj">%s</span>%s</div>\n'
            "          <div>\n"
            "            <h4>%s</h4>\n"
            "            <p>%s</p>\n"
            "          </div>\n"
            "        </div>"
            % (p["color"], e["version"], html.escape(p["name"]), date_html,
               inline(e["title"]), inline(e["summary"])))
    return "\n".join(blocks)


def splice(text, start, end, replacement):
    pattern = re.compile(r"(%s)(.*?)(%s)" % (re.escape(start), re.escape(end)), re.S)
    if not pattern.search(text):
        raise SystemExit("marker not found in index.html: " + start)
    return pattern.sub(lambda m: m.group(1) + replacement + m.group(3), text, count=1)


def main():
    store = load_store()
    for p in PROJECTS:
        store[p["key"]] = fetch_project(p, store.get(p["key"], []))

    with open(INDEX, encoding="utf-8") as f:   # universal newlines: CRLF checkouts read as LF
        page = f.read()
    new = splice(page, "<!--updates:start-->", "<!--updates:end-->",
                 "\n" + render_updates(store) + "\n        ")
    for p in PROJECTS:
        entries = store.get(p["key"], [])
        if entries:
            new = splice(new, "<!--ver:%s-->" % p["key"], "<!--/ver-->", "v" + entries[0]["version"])

    if new != page:
        with open(INDEX, "w", encoding="utf-8", newline="\n") as f:
            f.write(new)
        print("index.html updated")
    else:
        print("index.html already up to date")

    blob = json.dumps(store, indent=2, ensure_ascii=False) + "\n"
    try:
        with open(STORE, encoding="utf-8") as f:
            same = f.read() == blob
    except OSError:
        same = False
    if not same:
        with open(STORE, "w", encoding="utf-8", newline="\n") as f:
            f.write(blob)


if __name__ == "__main__":
    main()
