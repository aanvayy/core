"""Exercise reconciliation, publication guards, packaging and quiet notifications."""

import base64
import hashlib
import json
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import pipeline


class PipelineTests(unittest.TestCase):
    def test_stable_tags_only(self):
        self.assertEqual(pipeline.release_tuple("2026.11.2"), (2026, 11, 2))
        for tag in ("dev", "2026.12.0b1", "../main", "2026.11.2;echo bad"):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                pipeline.release_tuple(tag)

    def test_preserve_unmanaged_work(self):
        pipeline.check_existing_changes(
            [
                pipeline.STATE,
                ".github/workflows/ci.yaml",
                "homeassistant/components/lg_infrared/climate.py",
            ]
        )
        with self.assertRaises(ValueError):
            pipeline.check_existing_changes(["homeassistant/core.py"])

    def test_quiet_unchanged_release_and_pending_attention(self):
        state = {
            "upstream_tag": "2026.11.2",
            "pipeline_version": pipeline.PIPELINE_VERSION,
            "parent_commit": "a" * 40,
        }
        response = {"content": base64.b64encode(json.dumps(state).encode()).decode()}
        for issues, expected in (([], "false"), ([{"body": pipeline.MARKER}], "true")):
            with self.subTest(attention=bool(issues)):
                with (
                    patch.object(
                        pipeline,
                        "api",
                        side_effect=[
                            {"tag_name": "2026.11.2"},
                            response,
                            {"parents": [{"sha": "a" * 40}]},
                            issues,
                        ],
                    ),
                    patch.object(pipeline, "output") as output,
                ):
                    pipeline.plan()
                output.assert_called_once_with(needed=expected, tag="2026.11.2")

    def test_plan_rejects_downgrade(self):
        state = {"upstream_tag": "2026.11.2"}
        response = {"content": base64.b64encode(json.dumps(state).encode()).decode()}
        with (
            patch.object(
                pipeline, "api", side_effect=[{"tag_name": "2026.11.1"}, response]
            ),
            self.assertRaises(ValueError),
        ):
            pipeline.plan()

    def test_manual_branch_change_requires_tests(self):
        state = {
            "upstream_tag": "2026.11.2",
            "pipeline_version": pipeline.PIPELINE_VERSION,
            "parent_commit": "a" * 40,
        }
        response = {"content": base64.b64encode(json.dumps(state).encode()).decode()}
        with (
            patch.object(
                pipeline,
                "api",
                side_effect=[
                    {"tag_name": "2026.11.2"},
                    response,
                    {"parents": [{"sha": "b" * 40}]},
                    [],
                ],
            ),
            patch.object(pipeline, "output") as output,
        ):
            pipeline.plan()
        output.assert_called_once_with(needed="true", tag="2026.11.2")

    def make_repository(self, root, conflict=False, extra=False):
        root.mkdir()
        pipeline.run("git", "init", "-q", cwd=root)
        pipeline.run("git", "config", "user.name", "Test", cwd=root)
        pipeline.run("git", "config", "user.email", "test@example.invalid", cwd=root)
        source = root / "homeassistant/components/lg_infrared/example.py"
        source.parent.mkdir(parents=True)
        text = "".join(f"line {i}\n" for i in range(30))
        source.write_text(text)
        (root / ".python-version").write_text("3.14.10\n")
        (root / ".github/workflows").mkdir(parents=True)
        (root / ".github/workflows/ci.yml").write_text("old workflow\n")
        pipeline.run("git", "add", ".", cwd=root)
        pipeline.run("git", "commit", "-qm", "upstream base", cwd=root)
        base = pipeline.run("git", "rev-parse", "HEAD", cwd=root)
        pipeline.run("git", "switch", "-c", "next-release", cwd=root)
        source.write_text(
            text.replace("line 0\n" if conflict else "line 29\n", "upstream change\n")
        )
        pipeline.run("git", "commit", "-qam", "upstream update", cwd=root)
        (root / ".github/workflows/ci.yml").write_text("new upstream workflow\n")
        pipeline.run("git", "commit", "-qam", "upstream CI change", cwd=root)
        upstream = pipeline.run("git", "rev-parse", "HEAD", cwd=root)
        pipeline.run("git", "switch", "-c", pipeline.BRANCH, base, cwd=root)
        source.write_text(text.replace("line 0\n", "local feature\n"))
        pipeline.write_json(
            root / pipeline.STATE,
            {"upstream_tag": "2026.11.1", "upstream_commit": base, "revision": 1},
        )
        if extra:
            (root / "unexpected.txt").write_text("preserve me\n")
        pipeline.run("git", "add", ".", cwd=root)
        pipeline.run("git", "commit", "-qm", "local patches", cwd=root)
        old_head = pipeline.run("git", "rev-parse", "HEAD", cwd=root)
        return source, upstream, old_head

    def prepared(self, root, destination, upstream):
        original = pipeline.run

        def local_run(*args, **kwargs):
            if args[:2] == ("git", "fetch"):
                return ""
            if args == ("git", "rev-parse", "FETCH_HEAD^{commit}"):
                return upstream
            return original(*args, **kwargs)

        with (
            patch.object(pipeline, "run", side_effect=local_run),
            patch.object(pipeline, "output"),
        ):
            return pipeline.prepare(root, destination, "2026.11.2")

    def test_three_way_preserves_feature_and_new_upstream(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "core"
            source, upstream, old_head = self.make_repository(root)
            destination = Path(directory) / "candidate"
            metadata = self.prepared(root, destination, upstream)
            self.assertIn("local feature\n", source.read_text())
            self.assertIn("upstream change\n", source.read_text())
            self.assertEqual(metadata["old_head"], old_head)
            self.assertEqual(
                pipeline.run("git", "rev-parse", pipeline.BRANCH, cwd=root), old_head
            )
            self.assertEqual(
                hashlib.sha256(
                    (destination / "candidate.patch").read_bytes()
                ).hexdigest(),
                metadata["patch_sha256"],
            )

    def test_conflict_does_not_advance_managed_branch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "core"
            _, upstream, old_head = self.make_repository(root, conflict=True)
            with self.assertRaises(subprocess.CalledProcessError):
                self.prepared(root, Path(directory) / "candidate", upstream)
            self.assertEqual(
                pipeline.run("git", "rev-parse", pipeline.BRANCH, cwd=root), old_head
            )

    def test_prepare_refuses_to_drop_unexpected_work(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "core"
            _, upstream, old_head = self.make_repository(root, extra=True)
            with self.assertRaises(ValueError):
                self.prepared(root, Path(directory) / "candidate", upstream)
            self.assertEqual((root / "unexpected.txt").read_text(), "preserve me\n")
            self.assertEqual(
                pipeline.run("git", "rev-parse", "HEAD", cwd=root), old_head
            )

    def test_publication_preserves_workflows_and_fast_forwards(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "core"
            _, upstream, old_head = self.make_repository(root)
            destination = Path(directory) / "candidate"
            self.prepared(root, destination, upstream)
            publisher = Path(directory) / "publisher"
            pipeline.run(
                "git",
                "clone",
                "--shared",
                "--branch",
                pipeline.BRANCH,
                str(root),
                str(publisher),
                cwd=Path(directory),
            )
            pipeline.run("git", "config", "user.name", "Test", cwd=publisher)
            pipeline.run(
                "git", "config", "user.email", "test@example.invalid", cwd=publisher
            )
            with (
                patch.object(pipeline, "output"),
                patch.object(pipeline, "verify_publish_origin"),
            ):
                pipeline.publish(publisher, destination)
            published = pipeline.run("git", "rev-parse", pipeline.BRANCH, cwd=root)
            self.assertEqual(
                pipeline.run("git", "show", "-s", "--format=%P", published, cwd=root),
                f"{old_head} {upstream}",
            )
            self.assertEqual(
                (publisher / ".github/workflows/ci.yml").read_text(), "old workflow\n"
            )
            state = json.loads((publisher / pipeline.STATE).read_text())
            self.assertEqual(state["upstream_commit"], upstream)
            self.assertEqual(state["parent_commit"], old_head)

    def test_candidate_checksum_and_concurrency_guards(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pipeline.write_json(
                root / "candidate.json",
                {
                    "old_head": "a" * 40,
                    "upstream_tag": "2026.11.2",
                    "patch_sha256": "wrong",
                },
            )
            (root / "candidate.patch").write_text("modified after testing")
            for current in ("a" * 40, "b" * 40):
                with (
                    self.subTest(current=current),
                    patch.object(pipeline, "run", return_value=current) as run,
                    patch.object(pipeline, "verify_publish_origin"),
                    self.assertRaises(ValueError),
                ):
                    pipeline.publish(root, root)
                run.assert_called_once()

    def test_package_preserves_stock_translations_and_records_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "core"
            destination = Path(directory) / "candidate"
            root.mkdir()
            (root / "LICENSE.md").write_text("Apache-2.0\n")
            stock = {}
            for domain in pipeline.DOMAINS:
                component = root / f"homeassistant/components/{domain}"
                (component / "translations").mkdir(parents=True)
                (component / "manifest.json").write_text(json.dumps({"domain": domain}))
                (component / "__init__.py").write_text("patched = True\n")
                (component / "translations/en.json").write_text(
                    '{"name":"Patched English"}'
                )
                stock[f"homeassistant/components/{domain}/obsolete.py"] = (
                    b"removed by local patch"
                )
                stock[f"homeassistant/components/{domain}/manifest.json"] = json.dumps(
                    {"domain": domain}
                ).encode()
                stock[f"homeassistant/components/{domain}/translations/fr.json"] = (
                    b'{"name":"French"}'
                )
            pipeline.write_json(
                destination / "candidate.json",
                {"upstream_tag": "2026.11.2", "build_version": "2026.11.2.1"},
            )
            (destination / "candidate.patch").write_text("patch\n")
            with patch.object(pipeline, "wheel_files", return_value=stock):
                pipeline.package(root, destination)
            archive = destination / "ha-local-components-2026.11.2.1.zip"
            with zipfile.ZipFile(archive) as package:
                self.assertNotIn(
                    "custom_components/lg_infrared/obsolete.py", package.namelist()
                )
                self.assertEqual(
                    json.loads(
                        package.read("custom_components/lg_infrared/manifest.json")
                    )["version"],
                    "2026.11.2.1",
                )
                self.assertEqual(
                    package.read(
                        "custom_components/panasonic_viera/translations/fr.json"
                    ),
                    b'{"name":"French"}',
                )
                self.assertEqual(
                    package.read(
                        "custom_components/panasonic_viera/translations/en.json"
                    ),
                    b'{"name":"Patched English"}',
                )
                self.assertIn(
                    "lg_infrared", json.loads(package.read("metadata/upstream.json"))
                )
            self.assertIn(
                hashlib.sha256(archive.read_bytes()).hexdigest(),
                (destination / "SHA256SUMS").read_text(),
            )

    def test_success_is_quiet_and_repeated_failure_is_deduplicated(self):
        with patch.object(pipeline, "api", return_value=[]) as api:
            pipeline.notify({"build": "success"}, "2026.11.2")
        self.assertEqual(api.call_count, 1)
        failures = {"build": "failure"}
        signature = hashlib.sha256(
            json.dumps(
                {"tag": "2026.11.2", "failures": failures}, sort_keys=True
            ).encode()
        ).hexdigest()
        issue = {
            "number": 1,
            "body": f"{pipeline.MARKER}\n<!-- signature:{signature} -->",
        }
        with patch.object(pipeline, "api", return_value=[issue]) as api:
            pipeline.notify(failures, "2026.11.2")
        self.assertEqual(api.call_count, 1)

    def test_publish_destination_is_restricted(self):
        for origin in (
            "https://github.com/aanvayy/core.git",
            "git@github.com:aanvayy/core.git",
        ):
            with (
                self.subTest(origin=origin),
                patch.object(pipeline, "run", return_value=origin),
            ):
                pipeline.verify_publish_origin(Path("."))
        with (
            patch.object(
                pipeline,
                "run",
                return_value="https://github.com/home-assistant/core.git",
            ),
            self.assertRaises(ValueError),
        ):
            pipeline.verify_publish_origin(Path("."))

    def test_attention_targets_owner_only_and_recovery_closes_it(self):
        with (
            patch.dict("os.environ", {"GITHUB_RUN_ID": "123"}),
            patch.object(pipeline, "api", side_effect=[[], {}]) as api,
        ):
            pipeline.notify({"plan": "success", "build": "failure"}, "2026.11.2")
        self.assertEqual(api.call_args.args[0], f"repos/{pipeline.OWNER_REPO}/issues")
        self.assertEqual(api.call_args.args[1]["assignees"], ["aanvayy"])
        with patch.object(
            pipeline, "api", side_effect=[[{"number": 1, "body": pipeline.MARKER}], {}]
        ) as api:
            pipeline.notify({"build": "success", "publish": "success"}, "2026.11.2")
        self.assertEqual(api.call_args.args[1], {"state": "closed"})


if __name__ == "__main__":
    unittest.main()
