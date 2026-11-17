"""Reconcile and package two local HA patches; never access a home installation."""

import argparse
import base64
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

UPSTREAM = "home-assistant/core"
OWNER_REPO = "aanvayy/core"
BRANCH = "maintained/ha-release"
STATE = ".github/ha-local-patches-release.json"
DOMAINS = ("lg_infrared", "panasonic_viera")
SCOPES = tuple(
    f"{root}/{domain}/"
    for root in ("homeassistant/components", "tests/components")
    for domain in DOMAINS
)
PIPELINE_VERSION = 1
MARKER = "<!-- ha-local-patches-attention -->"


def is_attention_issue(issue):
    """Only manage this workflow bot's own issues, never user PRs or lookalikes."""
    return (
        MARKER in (issue.get("body") or "")
        and "pull_request" not in issue
        and issue.get("user", {}).get("login") == "github-actions[bot]"
    )


def run(*args, cwd, input=None, trim=True):
    """Run fixed commands without shell interpolation."""
    try:
        result = subprocess.run(
            args, cwd=cwd, input=input, text=True, capture_output=True, check=True
        ).stdout
    except subprocess.CalledProcessError as error:
        print(error.stdout or "", file=sys.stderr)
        print(error.stderr or "", file=sys.stderr)
        raise
    return result.strip() if trim else result


def api(path, data=None, method=None):
    """Use the job's narrowly scoped GitHub token."""
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "ha-local-patches",
        "Content-Type": "application/json",
    }
    if token := os.environ.get("GH_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        "https://api.github.com/" + path,
        data=None if data is None else json.dumps(data).encode(),
        headers=headers,
        method=method,
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def release_tuple(tag):
    """Accept stable HA release tags only."""
    if not re.fullmatch(r"20\d\d\.\d{1,2}\.\d+", tag):
        raise ValueError("Not a stable HA release tag")
    return tuple(map(int, tag.split(".")))


def sha(value):
    """Require a commit object identifier before using it in Git commands."""
    if not re.fullmatch(r"[0-9a-f]{40}", value):
        raise ValueError("Invalid commit identifier")
    return value


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def output(**values):
    """Return plan outputs to Actions, without logging any credentials."""
    if destination := os.environ.get("GITHUB_OUTPUT"):
        with open(destination, "a") as stream:
            stream.writelines(f"{key}={value}\n" for key, value in values.items())
    else:
        print(json.dumps(values))


def plan(force=False):
    """Skip unchanged releases; do not run the large test job unnecessarily."""
    latest = api(f"repos/{UPSTREAM}/releases/latest")["tag_name"]
    item = api(f"repos/{OWNER_REPO}/contents/{STATE}?ref={BRANCH}")
    state = json.loads(base64.b64decode(item["content"]))
    newer = release_tuple(latest) > release_tuple(state["upstream_tag"])
    if release_tuple(latest) < release_tuple(state["upstream_tag"]):
        raise ValueError("Refusing to downgrade the maintained HA release")
    head = api(f"repos/{OWNER_REPO}/commits/{BRANCH}")
    modified = not head["parents"] or head["parents"][0]["sha"] != state.get(
        "parent_commit"
    )
    attention = any(
        is_attention_issue(issue)
        for issue in api(f"repos/{OWNER_REPO}/issues?state=open&per_page=100")
    )
    needed = (
        force
        or newer
        or modified
        or attention
        or state.get("pipeline_version") != PIPELINE_VERSION
    )
    output(needed=str(needed).lower(), tag=latest)


def allowed(path):
    """Local feature work is restricted to the integrations and their tests."""
    return any(path.startswith(prefix) for prefix in SCOPES)


def check_existing_changes(paths):
    """Stop rather than silently discard unexpected work on the managed branch."""
    invalid = [
        p
        for p in paths
        if p != STATE and not p.startswith(".github/workflows/") and not allowed(p)
    ]
    if invalid:
        raise ValueError("Unmanaged changes must be reconciled: " + ", ".join(invalid))


def ensure_commit(core, commit):
    commit = sha(commit)
    try:
        run("git", "cat-file", "-e", commit + "^{commit}", cwd=core)
    except subprocess.CalledProcessError:
        run(
            "git",
            "fetch",
            "--depth=1",
            "https://github.com/" + UPSTREAM + ".git",
            commit,
            cwd=core,
        )
    return commit


def prepare(core, destination, tag):
    """Three-way apply the maintained delta onto the complete new HA release."""
    release_tuple(tag)
    previous = json.loads((core / STATE).read_text())
    if release_tuple(tag) < release_tuple(previous["upstream_tag"]):
        raise ValueError("Refusing to downgrade")
    old_head = sha(run("git", "rev-parse", "HEAD", cwd=core))
    old_base = ensure_commit(core, previous["upstream_commit"])
    changed = run(
        "git", "diff", "--name-only", old_base, old_head, cwd=core
    ).splitlines()
    check_existing_changes(changed)
    patch = run(
        "git",
        "diff",
        "--no-ext-diff",
        "--binary",
        old_base,
        old_head,
        "--",
        *SCOPES,
        cwd=core,
        trim=False,
    )
    run(
        "git",
        "fetch",
        "--depth=1",
        "https://github.com/" + UPSTREAM + ".git",
        "refs/tags/" + tag,
        cwd=core,
    )
    upstream = sha(run("git", "rev-parse", "FETCH_HEAD^{commit}", cwd=core))
    run("git", "checkout", "--detach", upstream, cwd=core)
    if patch.strip():
        run(
            "git",
            "apply",
            "--3way",
            "--index",
            "--whitespace=error-all",
            "-",
            cwd=core,
            input=patch,
        )
    candidate = run(
        "git",
        "diff",
        "--no-ext-diff",
        "--binary",
        upstream,
        "--",
        *SCOPES,
        cwd=core,
        trim=False,
    )
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "candidate.patch").write_text(candidate)
    revision = previous.get("revision", 0) + 1 if previous["upstream_tag"] == tag else 1
    metadata = {
        "old_head": old_head,
        "upstream_tag": tag,
        "upstream_commit": upstream,
        "revision": revision,
        "build_version": f"{tag}.{revision}",
        "pipeline_version": PIPELINE_VERSION,
        "patch_sha256": hashlib.sha256(candidate.encode()).hexdigest(),
    }
    write_json(destination / "candidate.json", metadata)
    output(python=(core / ".python-version").read_text().strip())
    return metadata


def install(core):
    """Install focused HA and test dependencies, not every HA integration."""
    dependencies = []
    for domain in (*DOMAINS, "homekit", "infrared", "broadlink"):
        dependencies.extend(
            json.loads(
                (core / f"homeassistant/components/{domain}/manifest.json").read_text()
            ).get("requirements", [])
        )
    run("uv", "venv", ".venv", cwd=core)
    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            ".venv/bin/python",
            "-e",
            ".",
            "-e",
            "packages/hassfest",
            "-e",
            "packages/pytest-homeassistant",
            "-r",
            "requirements_test.txt",
            *dependencies,
        ],
        cwd=core,
        check=True,
    )


def wheel_files(tag):
    """Verify the official HA wheel before retaining its stock translations."""
    release_tuple(tag)
    with urllib.request.urlopen(
        f"https://pypi.org/pypi/homeassistant/{tag}/json", timeout=60
    ) as response:
        metadata = json.load(response)
    wheel = next(item for item in metadata["urls"] if item["filename"].endswith(".whl"))
    if not wheel["url"].startswith("https://files.pythonhosted.org/"):
        raise ValueError("Unexpected HA package host")
    with urllib.request.urlopen(wheel["url"], timeout=120) as response:
        payload = response.read()
    if hashlib.sha256(payload).hexdigest() != wheel["digests"]["sha256"]:
        raise ValueError("HA wheel checksum mismatch")
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        return {
            info.filename: archive.read(info)
            for info in archive.infolist()
            if not info.is_dir()
            and any(
                info.filename.startswith(f"homeassistant/components/{domain}/")
                for domain in DOMAINS
            )
        }


def package(core, destination):
    """Create installable artifacts; never deploy to HA or change its settings."""
    metadata = json.loads((destination / "candidate.json").read_text())
    stock = wheel_files(metadata["upstream_tag"])
    payload = {}
    baselines = {}
    for domain in DOMAINS:
        prefix = f"homeassistant/components/{domain}/"
        baseline = {
            name.removeprefix(prefix): hashlib.sha256(value).hexdigest()
            for name, value in stock.items()
            if name.startswith(prefix)
        }
        baselines[domain] = {
            "repository": UPSTREAM,
            "ref": metadata["upstream_tag"],
            "path": prefix.rstrip("/"),
            "files_sha256": baseline,
        }
        files = {
            name.removeprefix(prefix): value
            for name, value in stock.items()
            if name.startswith(prefix + "translations/")
        }
        for path in (core / prefix).rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts:
                files[path.relative_to(core / prefix).as_posix()] = path.read_bytes()
        manifest = json.loads(files["manifest.json"])
        manifest["version"] = metadata["build_version"]
        files["manifest.json"] = (json.dumps(manifest, indent=2) + "\n").encode()
        payload.update(
            {
                f"custom_components/{domain}/{name}": value
                for name, value in files.items()
            }
        )
    payload["metadata/upstream.json"] = (
        json.dumps(baselines, indent=2, sort_keys=True) + "\n"
    ).encode()
    payload["metadata/build.json"] = (
        json.dumps(metadata, indent=2, sort_keys=True) + "\n"
    ).encode()
    payload["metadata/patches.diff"] = (destination / "candidate.patch").read_bytes()
    navigation = (
        core / "tests/components/panasonic_viera/fixtures/homekit_navigation.yaml"
    )
    if navigation.exists():
        payload["packages/panasonic_homekit_navigation.yaml"] = navigation.read_bytes()
    payload["LICENSE.md"] = (core / "LICENSE.md").read_bytes()
    archive_path = destination / f"ha-local-components-{metadata['build_version']}.zip"
    with zipfile.ZipFile(
        archive_path, "w", compression=zipfile.ZIP_DEFLATED
    ) as archive:
        for name, value in sorted(payload.items()):
            if Path(name).is_absolute() or ".." in Path(name).parts:
                raise ValueError("Invalid package path")
            info = zipfile.ZipInfo(name)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, value)
    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    (destination / "SHA256SUMS").write_text(f"{digest}  {archive_path.name}\n")


def verify_publish_origin(core):
    """Never write to upstream or another repository by accident."""
    origin = run("git", "remote", "get-url", "origin", cwd=core)
    approved = {
        f"https://github.com/{OWNER_REPO}",
        f"https://github.com/{OWNER_REPO}.git",
        f"git@github.com:{OWNER_REPO}.git",
    }
    if origin not in approved:
        raise ValueError("Refusing to publish to an unapproved repository")


def publish(core, destination):
    """Fast-forward only the managed branch after the separate test job passes."""
    verify_publish_origin(core)
    metadata = json.loads((destination / "candidate.json").read_text())
    release_tuple(metadata["upstream_tag"])
    old_head = sha(metadata["old_head"])
    if run("git", "rev-parse", "HEAD", cwd=core) != old_head:
        raise ValueError("Managed branch changed during testing; rerun")
    patch = (destination / "candidate.patch").read_bytes()
    if hashlib.sha256(patch).hexdigest() != metadata["patch_sha256"]:
        raise ValueError("Candidate checksum mismatch")
    upstream = ensure_commit(core, metadata["upstream_commit"])
    run("git", "checkout", "--detach", upstream, cwd=core)
    if patch.strip():
        run(
            "git",
            "apply",
            "--index",
            "--whitespace=error-all",
            "-",
            cwd=core,
            input=patch.decode(),
        )
    changes = run(
        "git", "diff", "--cached", "--name-only", upstream, cwd=core
    ).splitlines()
    if any(not allowed(path) for path in changes):
        raise ValueError("Candidate modifies files outside the feature scope")
    # Upstream CI is not executed here, and GITHUB_TOKEN need not edit workflows.
    run(
        "git",
        "restore",
        "--source",
        old_head,
        "--staged",
        "--worktree",
        ".github/workflows",
        cwd=core,
    )
    state = {key: value for key, value in metadata.items() if key != "old_head"}
    state["parent_commit"] = old_head
    state["artifact_run_id"] = os.environ.get("GITHUB_RUN_ID")
    state["artifact_url"] = os.environ.get("ARTIFACT_URL")
    write_json(core / STATE, state)
    run("git", "add", "--", STATE, cwd=core)
    tree = run("git", "write-tree", cwd=core)
    commit = run(
        "git",
        "commit-tree",
        tree,
        "-p",
        old_head,
        "-m",
        f"Maintain tested local patches for HA {metadata['upstream_tag']}",
        cwd=core,
    )
    run("git", "push", "origin", f"{commit}:refs/heads/{BRANCH}", cwd=core)
    output(commit=commit)


def notify(results, tag):
    """Assign one attention issue per failure state; routine success is silent."""
    failed = {
        name: value
        for name, value in results.items()
        if value in ("failure", "cancelled")
    }
    signature = hashlib.sha256(
        json.dumps({"tag": tag, "failures": failed}, sort_keys=True).encode()
    ).hexdigest()
    issues = api(f"repos/{OWNER_REPO}/issues?state=open&per_page=100")
    existing = next((issue for issue in issues if is_attention_issue(issue)), None)
    if not failed:
        if existing:
            api(
                f"repos/{OWNER_REPO}/issues/{existing['number']}",
                {"state": "closed"},
                "PATCH",
            )
        return
    if existing and f"<!-- signature:{signature} -->" in (existing.get("body") or ""):
        return
    run_url = (
        f"https://github.com/{OWNER_REPO}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
    )
    body = f"{MARKER}\n<!-- signature:{signature} -->\n\nLocal LG/Panasonic maintenance needs attention for HA `{tag or 'release lookup'}`.\n\nFailed stages: `{', '.join(failed)}`. [Inspect the run]({run_url}) for the conflict or failed check.\n\nThe maintained branch was not advanced past failed tests. No home installation was accessed or changed. Bring this issue to the maintenance chat for diagnosis; do not force-sync the feature branches."
    if existing:
        api(f"repos/{OWNER_REPO}/issues/{existing['number']}", {"body": body}, "PATCH")
    else:
        api(
            f"repos/{OWNER_REPO}/issues",
            {
                "title": "LG/Panasonic patch maintenance needs attention",
                "body": body,
                "assignees": [OWNER_REPO.split("/")[0]],
            },
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("plan", "prepare", "install", "package", "publish", "notify"),
    )
    parser.add_argument("--core", type=Path, default=Path("core"))
    parser.add_argument("--out", type=Path, default=Path("candidate"))
    parser.add_argument("--tag", default="")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if args.command == "plan":
        plan(args.force or os.environ.get("FORCE") == "true")
    elif args.command == "prepare":
        prepare(args.core.resolve(), args.out.resolve(), args.tag)
    elif args.command == "install":
        install(args.core.resolve())
    elif args.command == "package":
        package(args.core.resolve(), args.out.resolve())
    elif args.command == "publish":
        publish(args.core.resolve(), args.out.resolve())
    else:
        notify(json.loads(os.environ["JOB_RESULTS"]), args.tag)


if __name__ == "__main__":
    main()
