#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 XeniaCloud
# SPDX-License-Identifier: GPL-3.0-or-later
"""Xenia upstream watchdog (XEN-317).

Runs every two hours. Tells Vasilis when, and only when, he has to act:

  * labels each open upstream-release PR from its checks:
      needs-human       the PR is a draft (the bot left work for a person)
      ci-pending        checks still running
      ci-red            a check failed, timed out or was cancelled
      no-ci             no check ran at all an hour after the PR opened
      ready-for-review  every check passed: review and merge are his
  * flags CI that has silently stopped:
      a job queued longer than QUEUED_MAX_MIN (the single xcode-27 Mac Mini is
      off, locked or busy), a job running longer than RUNNING_MAX_MIN (a stalled
      step), the release workflow disabled (GitHub turns off schedules after 60
      days without activity in a public repo), or no successful release run in
      DETECTION_MAX_H hours (App key revoked, schedule dropped)
  * keeps ONE issue, labelled upstream-watch and assigned to xeniacloud, with the
    current alarms. A comment is posted only for alarms that are new since the last
    run, so each state change notifies once; the issue is closed when all is clear.

If the issue cannot be written (the App has no issues: write yet) the alarms are
printed as errors and the run fails, so the failure email still reaches someone.

Usage:
  python3 scripts/xenia_watchdog.py            check and notify (READ_TOKEN, GH_TOKEN)
  python3 scripts/xenia_watchdog.py --dry-run  check and print, write nothing
  python3 scripts/xenia_watchdog.py --self-test
"""

import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.error
import urllib.request

API = "https://api.github.com"
RELEASE_WORKFLOW = "xenia-upstream-release.yml"
STATE_LABELS = ("needs-human", "ci-pending", "ci-red", "no-ci", "ready-for-review")
ALARM_STATES = ("needs-human", "ci-red", "no-ci", "ready-for-review")
BAD_CONCLUSIONS = ("failure", "timed_out", "cancelled", "action_required", "startup_failure", "stale")
ISSUE_LABEL = "upstream-watch"
ASSIGNEE = "xeniacloud"
KEYS_RE = re.compile(r"<!-- xenia-watchdog-keys: (\[.*?\]) -->")


def env_int(name, default):
    return int(os.environ.get(name) or default)


def parse_time(value):
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def minutes_since(value, now):
    return (now - parse_time(value)).total_seconds() / 60


# --- pure logic (self-tested) ----------------------------------------------

def classify(pr, checks, now, no_ci_min):
    if pr.get("draft"):
        return "needs-human"
    if not checks:
        return "no-ci" if minutes_since(pr["created_at"], now) > no_ci_min else "ci-pending"
    if any(c.get("conclusion") in BAD_CONCLUSIONS for c in checks):
        return "ci-red"
    if any(c.get("status") != "completed" for c in checks):
        return "ci-pending"
    return "ready-for-review"


def pr_alarm(pr, state):
    n, url = pr["number"], pr["html_url"]
    text = {
        "needs-human": f"[#{n}]({url}) needs a person: conflicts or a guard failure the bot could not resolve (see the report in the PR).",
        "ci-red": f"[#{n}]({url}) has red CI. Read the failing job's log.",
        "no-ci": f"[#{n}]({url}) has no checks at all an hour after it opened: CI never triggered.",
        "ready-for-review": f"[#{n}]({url}) is green and ready for your review. Merge with \"Create a merge commit\" only.",
    }[state]
    # needs-human is keyed on the PR alone, so the person's own resolution pushes
    # do not re-notify; ci-red and ready-for-review are keyed on the commit, so a new
    # push that fails (or passes) again is news.
    key = f"{state}:#{n}" if state == "needs-human" else f"{state}:#{n}:{pr['head']['sha'][:12]}"
    return (key, text)


def job_alarms(jobs, now, queued_max, running_max):
    out = []
    for job in jobs:
        labels = ",".join(job.get("labels") or []) or "unknown"
        name = f"[{job.get('workflow_name') or 'job'} / {job['name']}]({job['html_url']})"
        if job["status"] in ("queued", "waiting", "pending") and minutes_since(job["created_at"], now) > queued_max:
            mins = int(minutes_since(job["created_at"], now))
            hint = " Check the Mac Mini (powered, screen not locked, runner service up); do not wait." if "xcode-27" in labels else ""
            out.append((f"queued:{job['id']}", f"{name} has been queued {mins} min on runner `{labels}`.{hint}"))
        elif job["status"] == "in_progress" and job.get("started_at") and minutes_since(job["started_at"], now) > running_max:
            mins = int(minutes_since(job["started_at"], now))
            hint = " Check the Mac Mini for a stalled step or a locked screen." if "xcode-27" in labels else ""
            out.append((f"running:{job['id']}", f"{name} has been running {mins} min on runner `{labels}`.{hint}"))
    return out


def detection_alarms(workflow, last_success, now, max_hours):
    if workflow is None:
        return [("release-workflow-missing", f"`{RELEASE_WORKFLOW}` is not in this repository's Actions.")]
    if workflow.get("state") != "active":
        return [(f"release-workflow-{workflow.get('state')}",
                 f"`{RELEASE_WORKFLOW}` is `{workflow.get('state')}`: no upstream release is being looked for. Re-enable it in Actions.")]
    if minutes_since(workflow["created_at"], now) < max_hours * 60:
        return []
    if last_success is None or minutes_since(last_success["created_at"], now) > max_hours * 60:
        when = last_success["created_at"] if last_success else "never"
        return [("detection-stale", f"No successful `{RELEASE_WORKFLOW}` run in {max_hours} h (last: {when}). Check its recent runs.")]
    return []


def previous_keys(issue):
    if not issue:
        return []
    m = KEYS_RE.search(issue.get("body") or "")
    return json.loads(m.group(1)) if m else []


def render_issue(alarms):
    lines = ["Current upstream-release items that need you. This issue is kept by "
             "`xenia-upstream-watchdog.yml` (XEN-317) and closes itself when nothing needs you.", ""]
    lines += [f"- {text}" for _, text in alarms]
    lines += ["", f"<!-- xenia-watchdog-keys: {json.dumps(sorted(k for k, _ in alarms))} -->"]
    return "\n".join(lines)


def plan_notification(issue, alarms):
    """Return the writes to make as (action, payload) tuples."""
    keys = sorted(k for k, _ in alarms)
    prev = previous_keys(issue)
    if not alarms:
        return [("close", None)] if issue else []
    if not issue:
        return [("create", render_issue(alarms))]
    actions = []
    if keys != sorted(prev):
        actions.append(("update", render_issue(alarms)))
    new = [text for k, text in alarms if k not in prev]
    if new:
        actions.append(("comment", f"@{ASSIGNEE} new since the last check:\n\n" + "\n".join(f"- {t}" for t in new)))
    return actions


# --- GitHub ----------------------------------------------------------------

class GitHub:
    # Reads use the workflow's GITHUB_TOKEN (actions, checks and pull-requests read),
    # writes use the App token so issue comments notify like a person's would.
    def __init__(self, read_token, write_token, repo):
        self.read_token, self.write_token, self.repo = read_token, write_token, repo

    def call(self, method, path, body=None):
        url = path if path.startswith("http") else f"{API}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": f"Bearer {self.read_token if method == 'GET' else self.write_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "xenia-upstream-watchdog",
        })
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            link = resp.headers.get("Link") or ""
        return (json.loads(raw) if raw else None), link

    def get(self, path):
        return self.call("GET", path)[0]

    def get_all(self, path, key=None):
        items, url = [], f"{API}{path}{'&' if '?' in path else '?'}per_page=100"
        while url:
            data, link = self.call("GET", url)
            items += data[key] if key else data
            m = re.search(r'<([^>]+)>;\s*rel="next"', link)
            url = m.group(1) if m else None
        return items


def collect(gh, now, cfg):
    repo = gh.repo
    alarms, label_changes = [], []
    for pr in gh.get_all(f"/repos/{repo}/pulls?state=open"):
        if not pr["head"]["ref"].startswith("upstream-release/"):
            continue
        checks = gh.get_all(f"/repos/{repo}/commits/{pr['head']['sha']}/check-runs", key="check_runs")
        state = classify(pr, checks, now, cfg["no_ci_min"])
        have = {label["name"] for label in pr.get("labels", [])}
        label_changes.append((pr["number"], state, sorted(have & set(STATE_LABELS) - {state}), state not in have))
        print(f"PR #{pr['number']}: {state} ({len(checks)} checks)")
        if state in ALARM_STATES:
            alarms.append(pr_alarm(pr, state))

    own_run = os.environ.get("GITHUB_RUN_ID", "")
    jobs = []
    for status in ("queued", "in_progress", "waiting"):
        for run in gh.get_all(f"/repos/{repo}/actions/runs?status={status}", key="workflow_runs"):
            if str(run["id"]) == own_run:
                continue
            for job in gh.get_all(f"/repos/{repo}/actions/runs/{run['id']}/jobs", key="jobs"):
                job.setdefault("workflow_name", run.get("name"))
                jobs.append(job)
    alarms += job_alarms(jobs, now, cfg["queued_max"], cfg["running_max"])

    try:
        workflow = gh.get(f"/repos/{repo}/actions/workflows/{RELEASE_WORKFLOW}")
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
        workflow = None
    last = None
    if workflow:
        runs = gh.get(f"/repos/{repo}/actions/workflows/{RELEASE_WORKFLOW}/runs?status=success&per_page=1")["workflow_runs"]
        last = runs[0] if runs else None
    alarms += detection_alarms(workflow, last, now, cfg["detection_max_h"])
    return alarms, label_changes


def apply(gh, alarms, label_changes):
    repo = gh.repo
    for number, state, remove, add in label_changes:
        for name in remove:
            gh.call("DELETE", f"/repos/{repo}/issues/{number}/labels/{name}")
        if add:
            gh.call("POST", f"/repos/{repo}/issues/{number}/labels", {"labels": ["upstream-release", state]})
    issues = [i for i in gh.get_all(f"/repos/{repo}/issues?state=open&labels={ISSUE_LABEL}") if "pull_request" not in i]
    issue = issues[0] if issues else None
    for action, payload in plan_notification(issue, alarms):
        if action == "create":
            gh.call("POST", f"/repos/{repo}/issues", {
                "title": "Upstream release needs you", "body": payload,
                "labels": [ISSUE_LABEL], "assignees": [ASSIGNEE]})
        elif action == "update":
            gh.call("PATCH", f"/repos/{repo}/issues/{issue['number']}", {"body": payload})
        elif action == "comment":
            gh.call("POST", f"/repos/{repo}/issues/{issue['number']}/comments", {"body": payload})
        elif action == "close":
            gh.call("POST", f"/repos/{repo}/issues/{issue['number']}/comments", {"body": "All clear: nothing needs you. Closing."})
            gh.call("PATCH", f"/repos/{repo}/issues/{issue['number']}", {"state": "closed", "state_reason": "completed"})
        print(f"issue: {action}")


# --- self-test -------------------------------------------------------------

def self_test():
    now = parse_time("2026-11-03T12:00:00Z")
    failures = []

    def expect(cond, msg):
        if not cond:
            failures.append(msg)

    pr = {"number": 7, "html_url": "u", "draft": False, "created_at": "2026-11-03T11:30:00Z", "head": {"sha": "a" * 40}}
    old_pr = dict(pr, created_at="2026-11-03T09:00:00Z")
    ok = {"status": "completed", "conclusion": "success"}
    expect(classify(dict(pr, draft=True), [ok], now, 60) == "needs-human", "draft is not needs-human")
    expect(classify(pr, [], now, 60) == "ci-pending", "fresh PR without checks is not pending")
    expect(classify(old_pr, [], now, 60) == "no-ci", "old PR without checks is not no-ci")
    expect(classify(pr, [ok, {"status": "in_progress", "conclusion": None}], now, 60) == "ci-pending", "running check is not pending")
    expect(classify(pr, [ok, {"status": "in_progress", "conclusion": None}, {"status": "completed", "conclusion": "failure"}], now, 60) == "ci-red", "a failure with others running is not red")
    expect(classify(pr, [ok, {"status": "completed", "conclusion": "skipped"}], now, 60) == "ready-for-review", "all passed is not ready")
    expect(classify(pr, [{"status": "completed", "conclusion": "cancelled"}], now, 60) == "ci-red", "cancelled is not red")

    job = {"id": 1, "name": "Build", "html_url": "j", "labels": ["xcode-27"], "workflow_name": "Build"}
    q = job_alarms([dict(job, status="queued", created_at="2026-11-03T09:00:00Z")], now, 120, 90)
    expect(len(q) == 1 and "Mac Mini" in q[0][1], "a 3 h queued xcode-27 job did not alarm")
    expect(job_alarms([dict(job, status="queued", created_at="2026-11-03T11:00:00Z")], now, 120, 90) == [], "a 1 h queue alarmed")
    r = job_alarms([dict(job, status="in_progress", created_at="2026-11-03T09:00:00Z", started_at="2026-11-03T10:00:00Z")], now, 120, 90)
    expect(len(r) == 1 and r[0][0] == "running:1", "a 2 h running job did not alarm")

    wf = {"state": "active", "created_at": "2026-10-01T00:00:00Z"}
    expect(detection_alarms(wf, {"created_at": "2026-11-03T02:00:00Z"}, now, 48) == [], "a recent success alarmed")
    expect(detection_alarms(wf, {"created_at": "2026-10-30T02:00:00Z"}, now, 48)[0][0] == "detection-stale", "a stale detection did not alarm")
    expect(detection_alarms(wf, None, now, 48)[0][0] == "detection-stale", "no success ever did not alarm")
    expect(detection_alarms(dict(wf, created_at="2026-11-03T00:00:00Z"), None, now, 48) == [], "a brand-new workflow alarmed")
    expect(detection_alarms(dict(wf, state="disabled_inactivity"), None, now, 48)[0][0].endswith("disabled_inactivity"), "a disabled workflow did not alarm")

    a1 = [("ready-for-review:#7:aaa", "ready"), ("queued:1", "queued")]
    expect([x for x, _ in plan_notification(None, a1)] == ["create"], "first alarm does not create the issue")
    issue = {"number": 3, "body": render_issue(a1)}
    expect(previous_keys(issue) == sorted(k for k, _ in a1), "keys do not round-trip through the issue body")
    expect(plan_notification(issue, a1) == [], "an unchanged state notified again")
    shrink = plan_notification(issue, a1[:1])
    expect([x for x, _ in shrink] == ["update"], "a resolved alarm should update without a comment")
    grow = plan_notification(issue, a1 + [("ci-red:#8:bbb", "red")])
    expect([x for x, _ in grow] == ["update", "comment"] and "red" in grow[1][1] and "ready" not in grow[1][1],
           "a new alarm did not comment exactly the new item")
    expect([x for x, _ in plan_notification(issue, [])] == ["close"], "all clear does not close")
    expect(plan_notification(None, []) == [], "all clear without an issue wrote something")

    for f in failures:
        print(f"::error::[watchdog self-test] {f}")
    if failures:
        return 1
    print("Watchdog self-test passed.")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    cfg = {
        "queued_max": env_int("QUEUED_MAX_MIN", 120),
        "running_max": env_int("RUNNING_MAX_MIN", 90),
        "no_ci_min": env_int("NO_CI_MIN", 60),
        "detection_max_h": env_int("DETECTION_MAX_H", 48),
    }
    gh = GitHub(os.environ["READ_TOKEN"], os.environ.get("GH_TOKEN", ""), os.environ["GITHUB_REPOSITORY"])
    now = dt.datetime.now(dt.timezone.utc)
    alarms, label_changes = collect(gh, now, cfg)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    text = "\n".join(f"- {t}" for _, t in alarms) or "Nothing needs attention."
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(f"### Upstream watchdog\n\n{text}\n")
    print(text)
    if args.dry_run:
        return 0
    try:
        apply(gh, alarms, label_changes)
    except urllib.error.HTTPError as exc:
        print(f"::error::[watchdog] could not write labels or the {ISSUE_LABEL} issue (HTTP {exc.code}). "
              "If 403: the xenia-upstream-sync App needs Issues: read and write.")
        for _, t in alarms:
            print(f"::error::[watchdog] {t}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
