#!/usr/bin/env python3
"""Regenerate the live-metrics block in README.md from the public GitHub API.

Every number written here can be re-derived by anyone from public data,
which is the point: the profile states its evidence, it doesn't assert it.

Usage:  GITHUB_TOKEN=... python scripts/update_metrics.py [--user KshitijV97]
Stdlib only, so the workflow needs no install step.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.github.com"
START, END = "<!-- METRICS:START -->", "<!-- METRICS:END -->"

# OSI-approved licences we expect to see; anything else (incl. NOASSERTION) is not counted.
OSI = {
    "MIT", "Apache-2.0", "AGPL-3.0", "GPL-3.0", "GPL-2.0", "LGPL-3.0", "LGPL-2.1",
    "MPL-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC", "Unlicense", "EPL-2.0", "0BSD",
}

# Root entries that show a repo is set up for a given coding agent.
AGENT_FILES = {
    "claude_code": {"CLAUDE.md", ".claude"},
    "opencode": {"opencode.json", "opencode.jsonc", ".opencode"},
    "antigravity": {".agent", "GEMINI.md"},
    "agents_md": {"AGENTS.md"},
}
TRAILER = re.compile(r"^co-authored-by:\s*(.+)$", re.I | re.M)
AGENT_TRAILER = re.compile(r"claude|opencode|gemini|antigravity", re.I)


def get(path: str, token: str):
    """GET a JSON resource, following Link pagination for list endpoints."""
    url = path if path.startswith("http") else f"{API}{path}"
    out, is_list = [], None
    while url:
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "profile-metrics",
        })
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.load(r)
                link = r.headers.get("Link", "")
        except urllib.error.HTTPError as e:
            if e.code in (404, 409):  # missing path / empty repo
                return [] if is_list is not False else None
            raise
        if not isinstance(data, list):
            return data
        is_list = True
        out.extend(data)
        m = re.search(r'<([^>]+)>;\s*rel="next"', link)
        url = m.group(1) if m else None
    return out


def collect(user: str, token: str) -> dict:
    now = dt.datetime.now(dt.timezone.utc)
    year_ago = now - dt.timedelta(days=365)
    ninety = now - dt.timedelta(days=90)

    repos = [r for r in get(f"/users/{user}/repos?type=owner&per_page=100", token)
             if not r["fork"] and not r["private"] and not r["archived"]]

    m = {k: 0 for k in AGENT_FILES}
    m.update(repos=len(repos), active90=0, active365=0, osi_active=0, ci_repos=0,
             workflows=0, commits=0, agent_commits=0, stars=0)

    for r in repos:
        name = r["name"]
        pushed = dt.datetime.fromisoformat(r["pushed_at"].replace("Z", "+00:00"))
        m["stars"] += r["stargazers_count"]
        if pushed < year_ago:
            continue  # old practice repos: counted in total, not scanned
        m["active365"] += 1
        m["active90"] += pushed >= ninety

        spdx = (r.get("license") or {}).get("spdx_id")
        m["osi_active"] += spdx in OSI

        root = {e["name"] for e in (get(f"/repos/{user}/{name}/contents/", token) or [])}
        for key, files in AGENT_FILES.items():
            m[key] += bool(root & files)

        wf = get(f"/repos/{user}/{name}/contents/.github/workflows", token) or []
        wf = [w for w in wf if w["name"].endswith((".yml", ".yaml"))]
        m["workflows"] += len(wf)
        m["ci_repos"] += bool(wf)

        since = year_ago.strftime("%Y-%m-%dT%H:%M:%SZ")
        for c in get(f"/repos/{user}/{name}/commits?author={user}&since={since}&per_page=100", token) or []:
            m["commits"] += 1
            if any(AGENT_TRAILER.search(t) for t in TRAILER.findall(c["commit"]["message"])):
                m["agent_commits"] += 1

    m["updated"] = now.strftime("%Y-%m-%d %H:%M UTC")
    return m


def render(m: dict, user: str) -> str:
    pct = lambda a, b: f"{round(100 * a / b)}%" if b else "–"
    rows = [
        ("Commits to my public repos, last 12 months", f"**{m['commits']:,}**",
         "author = me, non-fork repos"),
        ("…of which written with a coding agent", f"**{m['agent_commits']:,}** ({pct(m['agent_commits'], m['commits'])})",
         "commit carries a `Co-Authored-By` trailer naming the agent"),
        ("Active repos with an agent contract", f"**{m['agents_md']} AGENTS.md** · **{m['claude_code']} CLAUDE.md**",
         "file at repo root"),
        ("Repos configured for Claude Code / OpenCode / Antigravity",
         f"**{m['claude_code']}** / **{m['opencode']}** / **{m['antigravity']}**",
         "`CLAUDE.md`·`.claude/` / `opencode.json`·`.opencode/` / `.agent/`·`GEMINI.md`"),
        ("Active repos gated by CI", f"**{m['ci_repos']}** of {m['active365']} ({m['workflows']} workflows)",
         "`.github/workflows/*.yml`"),
        ("Active repos under an OSI-approved licence", f"**{m['osi_active']}** of {m['active365']}",
         "GitHub-detected SPDX id"),
        ("Repos pushed in the last 90 days", f"**{m['active90']}**", f"of {m['repos']} public originals"),
    ]
    lines = ["| Signal | Value | Measured as |", "|:--|:--|:--|"]
    lines += [f"| {a} | {b} | <sub>{c}</sub> |" for a, b, c in rows]
    lines.append("")
    lines.append(f"<sub>Regenerated daily from the public GitHub API by "
                 f"[`update_metrics.py`](scripts/update_metrics.py) · last run {m['updated']} · "
                 f"\"active\" = pushed in the last 12 months</sub>")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", default=os.environ.get("GITHUB_REPOSITORY_OWNER", "KshitijV97"))
    ap.add_argument("--readme", default="README.md")
    ap.add_argument("--json", help="also write raw metrics here")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("GITHUB_TOKEN is required", file=sys.stderr)
        return 1

    m = collect(args.user, token)
    readme = Path(args.readme)
    text = readme.read_text()
    if START not in text or END not in text:
        print(f"markers {START} / {END} not found in {readme}", file=sys.stderr)
        return 1
    head, rest = text.split(START, 1)
    _, tail = rest.split(END, 1)
    readme.write_text(f"{head}{START}\n{render(m, args.user)}\n{END}{tail}")
    if args.json:
        Path(args.json).write_text(json.dumps(m, indent=2) + "\n")
    print(json.dumps(m, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
