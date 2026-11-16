"""Report relevant upstream source changes in the owner's fork, never deploy them."""

import argparse
import hashlib
import json
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path

UPSTREAM = "home-assistant/core"
MARKER = "<!-- lg-infrared-upstream-watch -->"


def api(path, data=None, method=None):
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "lg-infrared-watch",
    }
    if token := os.environ.get("GH_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        "https://api.github.com/" + path,
        data=None if data is None else json.dumps(data).encode(),
        headers=headers,
        method=method,
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        return json.load(response)


def fingerprint(path, ref):
    content = api(
        f"repos/{UPSTREAM}/contents/{path}?ref={urllib.parse.quote(ref, safe='')}"
    )
    entries = content if isinstance(content, list) else [content]
    return hashlib.sha256(
        json.dumps(sorted((entry["name"], entry["sha"]) for entry in entries)).encode()
    ).hexdigest()


def inspect(baseline):
    release = api(f"repos/{UPSTREAM}/releases/latest")
    tag = release["tag_name"]
    if not re.fullmatch(r"\d{4}\.\d{1,2}\.\d+", tag):
        raise ValueError("Expected a stable Home Assistant release")
    changes = []
    for path, expected in baseline["paths"].items():
        for stream, ref in (("release", tag), ("dev", "dev")):
            actual = fingerprint(path, ref)
            if actual != expected[stream]:
                changes.append(
                    {"path": path, "stream": stream, "ref": ref, "fingerprint": actual}
                )
    return tag, changes


def notify(repository, baseline, tag, changes):
    owner = repository.split("/")[0]
    if repository == UPSTREAM or not re.fullmatch(
        r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository
    ):
        raise ValueError("Notifications must go to an owner fork, never upstream")
    body = MARKER + "\nRelevant source differs from the reviewed baseline.\n\n"
    body += (
        f"Deployed patch baseline: {baseline['release']}. Latest stable HA: {tag}.\n\n"
    )
    for change in changes:
        path, ref = change["path"], change["ref"]
        body += f"- [{path} ({change['stream']}, {ref})](https://github.com/{UPSTREAM}/tree/{ref}/{path}): `{change['fingerprint']}`\n"
    body += (
        "\nReview the changed source and dependency pins, port the sensor delta, run LG and transport tests, "
        "then validate and deliberately deploy. A dev change is an early heads-up, not a released HA change. "
        "Do not force-sync the patch branch or assume a PR merge updates the installed custom component.\n\n"
        "After review, update the baseline fingerprints in this fork and close this issue. "
        "This workflow only reads upstream and manages this notification; it never merges or deploys.\n"
    )
    issues = api(
        f"repos/{repository}/issues?state=open&per_page=100&creator=github-actions%5Bbot%5D"
    )
    existing = next(
        (issue for issue in issues if MARKER in (issue.get("body") or "")), None
    )
    if existing is None:
        issue = api(
            f"repos/{repository}/issues",
            {
                "title": "IR upstream changes need review",
                "body": body,
                "assignees": [owner],
            },
        )
        print(issue["html_url"])
    elif existing["body"] != body:
        api(f"repos/{repository}/issues/{existing['number']}/comments", {"body": body})
        api(f"repos/{repository}/issues/{existing['number']}", {"body": body}, "PATCH")
        print(existing["html_url"])
    else:
        print("Existing notification is current")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    baseline = json.loads(Path(__file__).with_name("baseline.json").read_text())
    tag, changes = inspect(baseline)
    print(json.dumps({"latest_release": tag, "changes": changes}, indent=2))
    if changes and not args.dry_run:
        notify(os.environ["GH_REPO"], baseline, tag, changes)


if __name__ == "__main__":
    main()
