#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 XeniaCloud
# SPDX-License-Identifier: GPL-3.0-or-later
"""Xenia upstream-release resolver (XEN-316).

Runs inside an in-progress `git merge --no-ff --no-commit <upstream release>`
and does three things, in this order:

  1. Restores .github/ to our side. A same-repo PR runs the workflow files from
     its own branch with this repository's secrets, so upstream workflow changes
     must never run unreviewed. What upstream changed there is listed in the
     report for a human to port.
  2. Resolves conflicts only where a rule in .github/xenia-resolve.json says the
     resolution is known to be safe. Anything else is left with its conflict
     markers for a human. Rules, first match wins, `never` beats every rule:
       ours            keep our side of the whole file (branding, docs)
       union           keep the lines of both sides (word lists)
       protected-keys  per conflict hunk: take our side only when every line on
                       both sides sets one of the listed keys (bundle id, team,
                       CalVer); other hunks stay for a human
  3. Checks that CURRENT_PROJECT_VERSION and MARKETING_VERSION are unchanged
     against our side in every .pbxproj and .xcconfig. CalVer is Vasilis's call,
     never an upstream merge's.

Exit status: 0 everything resolved and checked, 3 a human is needed (unresolved
conflicts or a CalVer change), 1 the resolver itself failed.

Usage:
  python3 scripts/xenia_resolve.py --report FILE   resolve the merge in progress
  python3 scripts/xenia_resolve.py --self-test      prove every rule still works

Same script in files-ios, talk-ios and notes-ios; only the JSON differs.
"""

import argparse
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

CONFIG_PATH = ".github/xenia-resolve.json"
VERSION_KEYS = ("CURRENT_PROJECT_VERSION", "MARKETING_VERSION")
VERSION_FILE_RE = re.compile(r"\.(pbxproj|xcconfig)$")
NEEDS_HUMAN = 3

OPEN, SEP, CLOSE, BASE = "<" * 7, "=" * 7, ">" * 7, "|" * 7


class ResolverError(Exception):
    pass


def git(root, *args, check=True, binary=False):
    proc = subprocess.run(["git", *args], cwd=root, capture_output=True, text=not binary)
    if check and proc.returncode != 0:
        err = proc.stderr if not binary else proc.stderr.decode("utf-8", "replace")
        raise ResolverError(f"git {' '.join(args)} failed: {err.strip()}")
    return proc.stdout


def glob_to_regex(pattern):
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
        elif pattern.startswith("**", i):
            out += ".*"
            i += 2
        elif pattern[i] == "*":
            out += "[^/]*"
            i += 1
        elif pattern[i] == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(pattern[i])
            i += 1
    return re.compile("^" + out + "$")


def matches(path, patterns):
    return any(glob_to_regex(p).match(path) for p in patterns)


def unmerged(root):
    """path -> {stage: blob sha} for every conflicted path."""
    entries = {}
    for rec in git(root, "ls-files", "-u", "-z").split("\0"):
        if not rec:
            continue
        meta, path = rec.split("\t", 1)
        _mode, sha, stage = meta.split()
        entries.setdefault(path, {})[int(stage)] = sha
    return entries


def remove_path(root, path):
    git(root, "rm", "-q", "-f", "--cached", "--ignore-unmatch", "--", path)
    full = os.path.join(root, path)
    if os.path.lexists(full):
        os.remove(full)


# --- step 1: .github --------------------------------------------------------

def restore_github(root):
    """Put .github/ back to our side; return upstream's changes there."""
    base = git(root, "merge-base", "HEAD", "MERGE_HEAD").strip()
    upstream_changes = [
        line for line in git(root, "diff", "--name-status", base, "MERGE_HEAD", "--", ".github/").splitlines() if line
    ]
    ours = set(git(root, "ls-tree", "-r", "--name-only", "HEAD", "--", ".github/").splitlines())
    index = set(git(root, "ls-files", "--", ".github/").splitlines())
    for path in sorted(index - ours):
        remove_path(root, path)
    if ours:
        git(root, "checkout", "HEAD", "--", ".github/")
    return upstream_changes


# --- step 2: rules ----------------------------------------------------------

def resolve_ours(root, path, stages):
    if 2 in stages:
        git(root, "checkout", "--ours", "--", path)
        git(root, "add", "--", path)
        return True, "kept our side"
    remove_path(root, path)
    return True, "kept our deletion"


def resolve_union(root, path, stages):
    if 2 not in stages or 3 not in stages:
        return False, "union needs both sides (one side deleted the file)"
    tmp = tempfile.mkdtemp()
    try:
        names = {}
        for stage, label in ((1, "base"), (2, "ours"), (3, "theirs")):
            names[label] = os.path.join(tmp, label)
            data = git(root, "cat-file", "blob", stages[stage], binary=True) if stage in stages else b""
            with open(names[label], "wb") as fh:
                fh.write(data)
        proc = subprocess.run(
            ["git", "merge-file", "--union", "-p", names["ours"], names["base"], names["theirs"]],
            capture_output=True,
        )
        if proc.returncode < 0 or proc.returncode > 127:
            return False, "git merge-file --union failed"
        with open(os.path.join(root, path), "wb") as fh:
            fh.write(proc.stdout)
    finally:
        shutil.rmtree(tmp)
    git(root, "add", "--", path)
    return True, "kept the lines of both sides"


def split_hunks(lines):
    """Yield ('text', [lines]) and ('hunk', ours, theirs, raw) in file order."""
    i, n = 0, len(lines)
    plain = []
    while i < n:
        if lines[i].startswith(OPEN + " ") or lines[i].rstrip("\r\n") == OPEN:
            if plain:
                yield ("text", plain)
                plain = []
            raw, ours, theirs, section = [lines[i]], [], [], "ours"
            i += 1
            while i < n:
                line = lines[i]
                raw.append(line)
                bare = line.rstrip("\r\n")
                if bare.startswith(BASE):
                    section = "base"
                elif bare == SEP:
                    section = "theirs"
                elif bare.startswith(CLOSE):
                    break
                elif section == "ours":
                    ours.append(line)
                elif section == "theirs":
                    theirs.append(line)
                i += 1
            else:
                # Unterminated hunk: treat as unresolvable.
                yield ("broken", raw)
                return
            yield ("hunk", ours, theirs, raw)
            i += 1
        else:
            plain.append(lines[i])
            i += 1
    if plain:
        yield ("text", plain)


def only_keys_differ(ours, theirs, key_re):
    """True when every line that differs between the two sides sets a protected key.

    Lines both sides have in common stay as they are, so a hunk that git widened
    over an unchanged neighbour (MARKETING_VERSION between two changed keys) still
    qualifies, while any other difference (SWIFT_VERSION, a new build phase) does not.
    """
    changed = []
    matcher = difflib.SequenceMatcher(a=ours, b=theirs, autojunk=False)
    for tag, a1, a2, b1, b2 in matcher.get_opcodes():
        if tag != "equal":
            changed += ours[a1:a2] + theirs[b1:b2]
    changed = [l for l in changed if l.strip()]
    return bool(changed) and all(key_re.match(l) for l in changed)


def resolve_protected_keys(root, path, stages, keys):
    if 2 not in stages or 3 not in stages:
        return False, "one side deleted the file"
    key_re = re.compile(r'^\s*"?(?:' + "|".join(re.escape(k) for k in keys) + r')(?:\[[^\]]*\])?"?\s*=')
    full = os.path.join(root, path)
    with open(full, encoding="utf-8", errors="surrogateescape", newline="") as fh:
        lines = fh.readlines()
    out, taken, left = [], 0, 0
    for part in split_hunks(lines):
        if part[0] == "text":
            out.extend(part[1])
        elif part[0] == "broken":
            return False, "unterminated conflict hunk"
        else:
            _, ours, theirs, raw = part
            if only_keys_differ(ours, theirs, key_re):
                out.extend(ours)
                taken += 1
            else:
                out.extend(raw)
                left += 1
    if taken == 0 and left == 0:
        return False, "no conflict hunks found"
    if taken:
        with open(full, "w", encoding="utf-8", errors="surrogateescape", newline="") as fh:
            fh.writelines(out)
    if left:
        return False, f"{left} hunk(s) touch more than {', '.join(keys)}; {taken} protected-key hunk(s) took our side"
    git(root, "add", "--", path)
    return True, f"{taken} hunk(s) setting only {', '.join(keys)}: took our side"


def resolve_conflicts(root, cfg):
    results = []
    for path, stages in sorted(unmerged(root).items()):
        if matches(path, cfg.get("never", [])):
            results.append((path, False, "never auto-resolved (listed under `never`)"))
            continue
        rule = next((r for r in cfg.get("rules", []) if matches(path, r["paths"])), None)
        if rule is None:
            results.append((path, False, "no rule, needs a human"))
            continue
        strategy = rule["strategy"]
        if strategy == "ours":
            ok, why = resolve_ours(root, path, stages)
        elif strategy == "union":
            ok, why = resolve_union(root, path, stages)
        elif strategy == "protected-keys":
            ok, why = resolve_protected_keys(root, path, stages, rule["keys"])
        else:
            raise ResolverError(f"unknown strategy {strategy!r} in {CONFIG_PATH}")
        results.append((path, ok, f"`{strategy}`: {why}"))
    return results


# --- step 3: CalVer ---------------------------------------------------------

VERSION_RE = re.compile(r'^\s*"?(' + "|".join(VERSION_KEYS) + r')(?:\[[^\]]*\])?"?\s*=\s*"?([^";\n]*?)"?\s*;?\s*$')


def version_settings(text):
    found = []
    for line in text.splitlines():
        m = VERSION_RE.match(line)
        if m:
            found.append(f"{m.group(1)} = {m.group(2)}")
    return sorted(found)


def check_versions(root):
    ours = {p for p in git(root, "ls-tree", "-r", "--name-only", "HEAD").splitlines() if VERSION_FILE_RE.search(p)}
    now = {p for p in git(root, "ls-files").splitlines() if VERSION_FILE_RE.search(p)}
    problems = []
    for path in sorted(ours | now):
        before = version_settings(git(root, "show", f"HEAD:{path}")) if path in ours else []
        after = []
        if path in now and os.path.isfile(os.path.join(root, path)):
            with open(os.path.join(root, path), encoding="utf-8", errors="replace") as fh:
                after = version_settings(fh.read())
        if before != after:
            added = sorted(set(after) - set(before))
            removed = sorted(set(before) - set(after))
            problems.append(f"`{path}`: version settings changed (added {added or 'none'}, removed {removed or 'none'})")
    return problems


# --- driver -----------------------------------------------------------------

def run(root, cfg):
    try:
        git(root, "rev-parse", "-q", "--verify", "MERGE_HEAD")
    except ResolverError:
        raise ResolverError("no merge in progress (MERGE_HEAD missing)")
    github_changes = restore_github(root)
    results = resolve_conflicts(root, cfg)
    version_problems = check_versions(root)
    left = sorted(unmerged(root))
    return github_changes, results, version_problems, left


def render_report(github_changes, results, version_problems, left):
    out = ["### Conflict resolution", ""]
    if not results:
        out.append("No conflicts.")
    else:
        out.append("| File | Result |")
        out.append("| --- | --- |")
        for path, ok, why in results:
            mark = "auto-resolved" if ok else "**needs a human**"
            out.append(f"| `{path}` | {mark}: {why} |")
    out += ["", "### CalVer", ""]
    if version_problems:
        out.append("**CURRENT_PROJECT_VERSION / MARKETING_VERSION changed against main. Restore ours; CalVer is not changed by an upstream merge.**")
        out += [f"- {p}" for p in version_problems]
    else:
        out.append("Unchanged against main.")
    out += ["", "### Upstream changes under `.github/` (not applied)", ""]
    if github_changes:
        out.append("Our `.github/` was kept as-is. Port anything wanted in a separate, reviewed PR.")
        out.append("")
        out.append("````text")
        out += github_changes
        out.append("````")
    else:
        out.append("None.")
    if left:
        out += ["", f"**{len(left)} file(s) still have conflict markers.** Resolve them on this branch."]
    return "\n".join(out) + "\n"


def load_config(root):
    with open(os.path.join(root, CONFIG_PATH), encoding="utf-8") as fh:
        return json.load(fh)


# --- self-test --------------------------------------------------------------

def self_test():
    env_keep = {k: os.environ.get(k) for k in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM")}
    os.environ["GIT_CONFIG_GLOBAL"] = os.devnull
    os.environ["GIT_CONFIG_NOSYSTEM"] = "1"
    tmp = tempfile.mkdtemp()
    failures = []

    def expect(cond, msg):
        if not cond:
            failures.append(msg)

    def write(path, text):
        full = os.path.join(tmp, path)
        os.makedirs(os.path.dirname(full) or tmp, exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(text)

    def read(path):
        with open(os.path.join(tmp, path), encoding="utf-8") as fh:
            return fh.read()

    def commit(msg):
        g("add", "-A")
        g("commit", "-q", "-m", msg)

    def g(*args, check=True):
        return git(tmp, "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", *args, check=check)

    pbx = ("\t\tCURRENT_PROJECT_VERSION = {v};\n\t\tMARKETING_VERSION = 1.0;\n"
           "\t\tPRODUCT_BUNDLE_IDENTIFIER = {b};\n\t\tSWIFT_VERSION = {s};\n")
    try:
        g("init", "-q", "-b", "main")
        write("Brand/Brand.swift", "let colour = \"base\"\n")
        write("App.xcodeproj/project.pbxproj", pbx.format(v="1", b="com.base", s="5.0"))
        write("words.txt", "alpha\nbeta\n")
        write("Source/App.swift", "let a = 1\n")
        write("Notes.xcconfig", "MARKETING_VERSION = 1.0\nOTHER = 1\n")
        write(".github/workflows/ci.yml", "base\n")
        write(CONFIG_PATH, json.dumps({
            "never": ["Source/**"],
            "rules": [
                {"paths": ["Brand/**"], "strategy": "ours"},
                {"paths": ["words.txt"], "strategy": "union"},
                {"paths": ["**/*.pbxproj"], "strategy": "protected-keys",
                 "keys": ["CURRENT_PROJECT_VERSION", "PRODUCT_BUNDLE_IDENTIFIER"]},
            ],
        }))
        commit("base")
        g("checkout", "-q", "-b", "upstream")
        write("Brand/Brand.swift", "let colour = \"nextcloud blue\"\n")
        write("App.xcodeproj/project.pbxproj", pbx.format(v="99", b="com.nextcloud", s="5.0"))
        write("words.txt", "alpha\nbeta\nupstreamword\n")
        write("Source/App.swift", "let a = 3\n")
        write(".github/workflows/ci.yml", "upstream\n")
        write(".github/workflows/new.yml", "upstream adds a workflow\n")
        commit("upstream release")
        g("checkout", "-q", "main")
        write("Brand/Brand.swift", "let colour = \"xenia\"\n")
        write("App.xcodeproj/project.pbxproj", pbx.format(v="26.11.3", b="eu.xeniacloud.x", s="5.0"))
        write("words.txt", "alpha\nbeta\nxenia\n")
        write("Source/App.swift", "let a = 2\n")
        write(".github/workflows/ci.yml", "ours\n")
        commit("xenia")

        # Case 1: safe hotspots resolve, Swift stays, .github is ours.
        g("merge", "--no-ff", "--no-commit", "upstream", check=False)
        cfg = load_config(tmp)
        gh, results, versions, left = run(tmp, cfg)
        by = {p: ok for p, ok, _ in results}
        expect(by.get("Brand/Brand.swift") is True, "ours rule did not resolve Brand/Brand.swift")
        expect("xenia" in read("Brand/Brand.swift"), "ours rule did not keep our branding")
        expect(by.get("words.txt") is True, "union rule did not resolve words.txt")
        expect("xenia" in read("words.txt") and "upstreamword" in read("words.txt"), "union lost a side")
        expect(by.get("App.xcodeproj/project.pbxproj") is True, "protected-keys did not resolve a key-only hunk")
        pb = read("App.xcodeproj/project.pbxproj")
        expect("26.11.3" in pb and "eu.xeniacloud.x" in pb and "nextcloud" not in pb, "protected-keys did not keep our values")
        expect(by.get("Source/App.swift") is False, "a `never` path was auto-resolved")
        expect(left == ["Source/App.swift"], f"unexpected unresolved set {left}")
        expect(read(".github/workflows/ci.yml") == "ours\n", ".github was not restored to our side")
        expect(not os.path.exists(os.path.join(tmp, ".github/workflows/new.yml")), "an upstream-added workflow survived")
        expect(any("new.yml" in c for c in gh), "upstream .github changes were not reported")
        expect(versions == [], f"false CalVer alarm: {versions}")
        report = render_report(gh, results, versions, left)
        expect("needs a human" in report and "new.yml" in report, "report is missing its sections")
        g("merge", "--abort")

        # Case 2: a hunk that mixes a protected key with anything else stays for a human,
        # and a clean (conflict-free) upstream CalVer change is caught.
        g("checkout", "-q", "-b", "upstream2", "upstream")
        write("App.xcodeproj/project.pbxproj", pbx.format(v="99", b="com.nextcloud", s="6.0"))
        write("Notes.xcconfig", "MARKETING_VERSION = 9.9\nOTHER = 1\n")
        commit("upstream mixes keys")
        g("checkout", "-q", "main")
        write("App.xcodeproj/project.pbxproj", pbx.format(v="26.11.3", b="eu.xeniacloud.x", s="5.1"))
        commit("ours touches swift version")
        g("merge", "--no-ff", "--no-commit", "upstream2", check=False)
        gh, results, versions, left = run(tmp, load_config(tmp))
        by = {p: ok for p, ok, _ in results}
        expect(by.get("App.xcodeproj/project.pbxproj") is False, "protected-keys resolved a mixed hunk")
        expect("App.xcodeproj/project.pbxproj" in left, "mixed pbxproj hunk was not left for a human")
        expect(any("Notes.xcconfig" in v for v in versions), "upstream MARKETING_VERSION change was not caught")
        g("merge", "--abort")
    except ResolverError as exc:
        failures.append(f"self-test could not run: {exc}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        for k, v in env_keep.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    # The repository's own rules must at least parse and name known strategies.
    try:
        cfg = load_config(".")
        for rule in cfg.get("rules", []):
            if rule.get("strategy") not in ("ours", "union", "protected-keys"):
                failures.append(f"{CONFIG_PATH}: unknown strategy {rule.get('strategy')!r}")
            if rule.get("strategy") == "protected-keys" and not rule.get("keys"):
                failures.append(f"{CONFIG_PATH}: protected-keys rule without keys")
    except (OSError, ValueError) as exc:
        failures.append(f"{CONFIG_PATH} unreadable: {exc}")

    for f in failures:
        print(f"::error::[resolver self-test] {f}")
    if failures:
        return 1
    print("Resolver self-test passed.")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--report", help="write the markdown resolution report here")
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    try:
        gh, results, versions, left = run(".", load_config("."))
    except (ResolverError, OSError, ValueError) as exc:
        print(f"::error::[resolver] {exc}")
        return 1
    report = render_report(gh, results, versions, left)
    if args.report:
        with open(args.report, "w", encoding="utf-8") as fh:
            fh.write(report)
    print(report)
    if left or versions:
        print(f"::warning::[resolver] needs a human: {len(left)} unresolved file(s), {len(versions)} CalVer change(s)")
        return NEEDS_HUMAN
    return 0


if __name__ == "__main__":
    sys.exit(main())
