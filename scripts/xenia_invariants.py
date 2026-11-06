#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 XeniaCloud
# SPDX-License-Identifier: GPL-3.0-or-later
"""Xenia invariants guard (XEN-308).

Fails when the tree breaks something this fork must keep, whatever route the
change took (an upstream sync merge, a conflict resolution, a hand edit).
Conflict review alone misses some of these: on files-ios #35 the auto-merge
silently re-added a "Crashlytics dSYM Upload" build phase.

Checks, configured per repository in .github/xenia-invariants.json:
  sdks            no Firebase / Crashlytics / Google Analytics / Google Ads SDKs
  bundle-ids      every bundle id starts with the Xenia prefix
  entitlements    App Groups, keychain groups and iCloud containers are ours
  background      no "location" in UIBackgroundModes
  login           the locked login URL is still present
  brand-colours   no Nextcloud brand blues in shipped source (fail or warn)

Usage:
  python3 scripts/xenia_invariants.py            check the repository
  python3 scripts/xenia_invariants.py --self-test prove every check still fires

Same script in files-ios, talk-ios and notes-ios; only the JSON differs.
"""

import json
import os
import plistlib
import re
import subprocess
import sys
import tempfile

CONFIG_PATH = ".github/xenia-invariants.json"

SDK_FILE_RE = re.compile(
    r"(\.pbxproj|Package\.resolved|Package\.swift|Podfile|Podfile\.lock|Cartfile(\.resolved)?)$"
)
SDK_RE = re.compile(
    r"firebase|crashlytics|googleappmeasurement|google-mobile-ads|googlemobileads"
    r"|google-ads-on-device|googleadsondevice|app-check|appcheck",
    re.IGNORECASE,
)
SOURCE_FILE_RE = re.compile(r"\.(swift|m|mm|h)$")
SDK_IMPORT_RE = re.compile(
    r"^\s*(@?import|#import|#include)\s+[<\"]?(Firebase\w*|GoogleMobileAds|GoogleAppMeasurement\w*)\b"
)
BUNDLE_SETTING_RE = re.compile(r"PRODUCT_BUNDLE_IDENTIFIER\s*=\s*\"?([^\";\n]+?)\"?\s*;?\s*$")
BRAND_BLUE_RE = re.compile(r"0082c9|00679e|006aa3", re.IGNORECASE)
TEXT_EXT_RE = re.compile(
    r"\.(swift|m|mm|h|c|plist|json|svg|strings|xib|storyboard|xcconfig|html|css|js|txt|md|rtf)$",
    re.IGNORECASE,
)


class Report:
    def __init__(self, quiet=False):
        self.errors = 0
        self.warnings = 0
        # The self-test feeds deliberate violations; printing them as ::error would
        # leave red annotations on a green run.
        self.quiet = quiet

    @staticmethod
    def _escape(text):
        return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")

    def error(self, check, msg, path=None, line=None):
        self.errors += 1
        self._emit("error", check, msg, path, line)

    def warn(self, check, msg, path=None, line=None):
        self.warnings += 1
        self._emit("warning", check, msg, path, line)

    def _emit(self, level, check, msg, path, line):
        if self.quiet:
            return
        loc = ""
        if path:
            loc = f" file={path}"
            if line:
                loc += f",line={line}"
        print(f"::{level}{loc}::[{check}] {self._escape(msg)}")


def read_text(root, path):
    try:
        with open(os.path.join(root, path), encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except (IsADirectoryError, FileNotFoundError):
        return ""


def read_plist(root, path, report, check):
    try:
        with open(os.path.join(root, path), "rb") as fh:
            return plistlib.load(fh)
    except Exception as exc:  # noqa: BLE001 - any unreadable plist is reported, not fatal
        report.warn(check, f"could not parse plist ({exc.__class__.__name__}), not checked", path)
        return None


def check_sdks(root, files, cfg, report):
    for path in files:
        name = os.path.basename(path)
        if name == "GoogleService-Info.plist":
            report.error("sdks", "GoogleService-Info.plist is tracked (Firebase configuration)", path)
            continue
        if SDK_FILE_RE.search(path):
            for no, line in enumerate(read_text(root, path).splitlines(), 1):
                if SDK_RE.search(line):
                    report.error("sdks", f"forbidden SDK reference: {line.strip()[:160]}", path, no)
        elif SOURCE_FILE_RE.search(path):
            for no, line in enumerate(read_text(root, path).splitlines(), 1):
                if SDK_IMPORT_RE.search(line):
                    report.error("sdks", f"forbidden SDK import: {line.strip()[:160]}", path, no)


def check_bundle_ids(root, files, cfg, report):
    prefix = cfg["bundle_id_prefix"]
    seen = 0
    for path in files:
        if path.endswith(".pbxproj") or path.endswith(".xcconfig"):
            for no, line in enumerate(read_text(root, path).splitlines(), 1):
                m = BUNDLE_SETTING_RE.search(line)
                if not m:
                    continue
                seen += 1
                value = m.group(1).strip()
                if not value.startswith(prefix):
                    report.error("bundle-ids", f"bundle id '{value}' does not start with '{prefix}'", path, no)
    if seen == 0:
        report.error("bundle-ids", "no PRODUCT_BUNDLE_IDENTIFIER found at all: the check cannot see the project")


def check_entitlements(root, files, cfg, report):
    prefix = cfg["bundle_id_prefix"]
    allowed_groups = set(cfg["allowed_app_groups"])
    found = 0
    for path in files:
        if not path.endswith(".entitlements"):
            continue
        found += 1
        data = read_plist(root, path, report, "entitlements")
        if not isinstance(data, dict):
            continue
        for group in data.get("com.apple.security.application-groups", []) or []:
            if group not in allowed_groups:
                report.error(
                    "entitlements",
                    f"App Group '{group}' is not allowed (allowed: {', '.join(sorted(allowed_groups))})",
                    path,
                )
        for group in data.get("keychain-access-groups", []) or []:
            bare = group.replace("$(AppIdentifierPrefix)", "")
            if not (bare.startswith(prefix) or bare in allowed_groups):
                report.error("entitlements", f"keychain group '{group}' is not a Xenia group", path)
        for key in ("com.apple.developer.icloud-container-identifiers",
                    "com.apple.developer.ubiquity-container-identifiers"):
            for container in data.get(key, []) or []:
                if not container.startswith("iCloud." + prefix):
                    report.error("entitlements", f"iCloud container '{container}' is not a Xenia container", path)
    if found == 0:
        report.error("entitlements", "no .entitlements files found: the check cannot see the project")


def check_background_modes(root, files, cfg, report):
    for path in files:
        if not path.endswith(".plist"):
            continue
        if "UIBackgroundModes" not in read_text(root, path):
            continue
        data = read_plist(root, path, report, "background")
        if isinstance(data, dict) and "location" in (data.get("UIBackgroundModes") or []):
            report.error("background", "UIBackgroundModes contains 'location' (location background upload stays out)", path)


def check_login(root, files, cfg, report):
    login = cfg.get("login")
    if not login:
        return
    path, needle = login["file"], login["contains"]
    if path not in files:
        report.error("login", f"{path} is missing, so the locked login URL cannot be confirmed")
        return
    if needle not in read_text(root, path):
        report.error("login", f"locked login no longer present: expected the text {needle!r}", path)


def check_brand_colours(root, files, cfg, report):
    brand = cfg.get("brand_colours")
    if not brand:
        return
    mode = brand.get("mode", "fail")
    dirs = tuple(d.rstrip("/") + "/" for d in brand["shipped_dirs"])
    for path in files:
        if not path.startswith(dirs) or not TEXT_EXT_RE.search(path):
            continue
        for no, line in enumerate(read_text(root, path).splitlines(), 1):
            if BRAND_BLUE_RE.search(line):
                msg = f"Nextcloud brand blue in shipped source: {line.strip()[:160]}"
                if mode == "fail":
                    report.error("brand-colours", msg, path, no)
                else:
                    report.warn("brand-colours", msg, path, no)


CHECKS = [
    ("sdks", check_sdks),
    ("bundle-ids", check_bundle_ids),
    ("entitlements", check_entitlements),
    ("background", check_background_modes),
    ("login", check_login),
    ("brand-colours", check_brand_colours),
]


def run(root, files, cfg, quiet=False):
    report = Report(quiet=quiet)
    for _, fn in CHECKS:
        fn(root, files, cfg, report)
    return report


def tracked_files(root):
    out = subprocess.run(["git", "ls-files", "-z"], cwd=root, check=True, capture_output=True).stdout
    return [p for p in out.decode("utf-8").split("\0") if p]


def self_test():
    """Build a clean fixture and one violation per check; every check must fire."""
    cfg = {
        "bundle_id_prefix": "eu.xeniacloud.test",
        "allowed_app_groups": ["group.eu.xeniacloud.test"],
        "login": {"file": "Brand/Brand.swift", "contains": 'loginBaseUrl = "https://portal.xeniacloud.eu"'},
        "brand_colours": {"mode": "fail", "shipped_dirs": ["App"]},
    }
    ent_ok = plistlib.dumps({"com.apple.security.application-groups": ["group.eu.xeniacloud.test"],
                             "keychain-access-groups": ["$(AppIdentifierPrefix)eu.xeniacloud.test"]})
    info_ok = plistlib.dumps({"UIBackgroundModes": ["fetch", "remote-notification"]})
    clean = {
        "App.xcodeproj/project.pbxproj": "PRODUCT_BUNDLE_IDENTIFIER = eu.xeniacloud.test.Share;\n",
        "App.entitlements": ent_ok,
        "App/Info.plist": info_ok,
        "App/View.swift": "import UIKit\nlet c = \"#123456\"\n",
        "Brand/Brand.swift": 'let loginBaseUrl = "https://portal.xeniacloud.eu"\n',
    }
    violations = {
        "sdks": {"App.xcodeproj/project.pbxproj":
                 "PRODUCT_BUNDLE_IDENTIFIER = eu.xeniacloud.test;\n/* Crashlytics dSYM Upload */\n"},
        "sdks-import": {"App/View.swift": "import FirebaseCore\n"},
        "bundle-ids": {"App.xcodeproj/project.pbxproj": "PRODUCT_BUNDLE_IDENTIFIER = \"it.twsweb.Nextcloud\";\n"},
        "entitlements": {"App.entitlements":
                         plistlib.dumps({"com.apple.security.application-groups": ["group.it.twsweb.Crypto-Cloud"]})},
        "background": {"App/Info.plist": plistlib.dumps({"UIBackgroundModes": ["fetch", "location"]})},
        "login": {"Brand/Brand.swift": 'let loginBaseUrl = "https://cloud.nextcloud.com"\n'},
        "brand-colours": {"App/View.swift": "let c = \"#0082C9\"\n"},
    }

    def build(tree):
        tmp = tempfile.mkdtemp(prefix="xenia-invariants-")
        for rel, content in tree.items():
            full = os.path.join(tmp, rel)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "wb") as fh:
                fh.write(content if isinstance(content, bytes) else content.encode())
        return tmp, sorted(tree)

    failures = []
    root, files = build(clean)
    print("--- self-test: clean fixture (expect no errors)")
    if run(root, files, cfg, quiet=True).errors:
        failures.append("clean fixture reported errors")
    for name, override in violations.items():
        tree = dict(clean)
        tree.update(override)
        root, files = build(tree)
        print(f"--- self-test: violation '{name}' (expect an error)")
        if run(root, files, cfg, quiet=True).errors == 0:
            failures.append(f"violation '{name}' was not detected")
    if failures:
        for f in failures:
            print(f"::error::[self-test] {f}. The guard is broken: fix it rather than ignoring this run.")
        return 1
    print("Self-test passed: every check fires on its violation and the clean fixture passes.")
    return 0


def main():
    if "--self-test" in sys.argv[1:]:
        return self_test()
    root = subprocess.run(["git", "rev-parse", "--show-toplevel"], check=True,
                          capture_output=True, text=True).stdout.strip()
    with open(os.path.join(root, CONFIG_PATH), encoding="utf-8") as fh:
        cfg = json.load(fh)
    report = run(root, tracked_files(root), cfg)
    summary = f"Xenia invariants: {report.errors} error(s), {report.warnings} warning(s)."
    print(summary)
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as fh:
            fh.write(summary + "\n")
    return 1 if report.errors else 0


if __name__ == "__main__":
    sys.exit(main())
