# Tested HA patch maintenance

This controller maintains `lg_infrared` (optional room sensor) and
`panasonic_viera` (named legacy inputs and native navigation) in the owner's
`aanvayy/core` fork. It does not connect to Home Assistant or operate appliances.

## Operation

- GitHub workflow: `.github/workflows/ha-local-patches.yml`.
- Controller and unit tests: `.github/ha-local-patches/` on the fork's default branch.
- Managed source: `maintained/ha-release`; contribution branches remain separate.
- Check for a new stable HA release daily at 03:17 UTC / 08:47 IST.
  Skip installation/build/tests when unchanged and healthy.
- Derive the local source/test delta from the last tested release, then apply it
  with Git's three-way merge onto the complete new release. Preserve unexpected
  branch work by stopping rather than dropping it.
- Run both upstream integration suites, feature regressions, stock HomeKit TV
  tests, lint/type checks and integration validation against that new framework.
- Only after success, fast-forward the managed branch with the tested source.
  The test job has read-only repository access. Publication has its own job,
  checksums and concurrent-change guard. No force-push is used.
- Upstream CI workflows are retained unchanged on the managed branch; they are
  not run or overwritten by this controller. This avoids requiring a PAT or
  workflow-edit permission for the Actions token. The controller workflow lives
  separately on the fork's default branch.

The managed branch is for builds, not an upstream PR. Its merge commits preserve
both the previous maintained commit and new HA release as parents. Feature
submission still requires the owner's review; no upstream PR is auto-created.

## Alerts and recovery

Routine successful/unchanged runs create no issue, PR, release or comment.
A conflict, failed check or publication/lookup failure creates one attention
issue assigned to the owner in their fork. Repeated identical failures are
quiet; a changed failure updates that issue. Successful recovery closes it.
Attention issues cause subsequent runs to retry even if the release is unchanged.
GitHub's own notification delivery follows the owner's account settings.

A manual run has Force rebuild and Notify-on-failure inputs. Use notification
suppression only while actively validating the initial setup; scheduled runs
always report failures. `pipeline.py` and `test_pipeline.py` include no secrets.

GitHub can disable public scheduled workflows after 60 inactive days. Successful
release commits provide ordinary repository activity, but this is not a promise
that GitHub will always run on time. The HA pre-upgrade reminder remains the
independent backstop: inspect the workflow/build if an upgrade is offered and
no compatible artifact is available.

## Artifacts and installation

Each successful build retains its ZIP, SHA256SUMS, canonical source patch,
upstream file hashes/build metadata and test reports as an Actions artifact for
90 days. A force rebuild can regenerate an expired artifact.

The ZIP contains the two complete custom component directories. Stock
translations come from the exact official HA wheel, verified against PyPI's
SHA-256; patched English translations come from HA's generator. It also includes
the tested, generic standard HomeKit-navigation package. There are no household
IDs, credentials, registries or pairing data in these public artifacts.

Publishing a build does **not** install it. Before a household HA upgrade:

1. Select the successful build matching the intended stable HA release and
   verify SHA256SUMS/build metadata. Never use the contribution/dev branch as
   a household release.
2. Follow the site/component installation and private-backup procedure. Install
   only the two documented custom component directories and, where needed,
   `packages/panasonic_homekit_navigation.yaml`; do not overlay the whole ZIP
   onto the HA configuration blindly.
3. Validate configuration, coordinate a restart, preserve entry/entity IDs and
   pairings, then verify normal states, room temperature and owner-observed
   device/Apple behavior where relevant.

The source baseline in `metadata/upstream.json` records the unmodified release;
custom manifests carry a release/rebuild version. No artifact writes back to a
home installation, private recovery repository or contribution branch.
