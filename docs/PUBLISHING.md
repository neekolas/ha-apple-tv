# Publishing the repository

Publish the reviewed tree as a single initial commit in a new repository. The existing Git history contains household data, including values already removed from the app.

## Current tree

The app ships with an empty TV host, identifier and profile settings. The live spike uses a documentation address by default. Set `ATV_BRIDGE_HOST` to select a real target. The deep-link validator accepts an optional `ATV_WAKE_PROFILE` and selects it only when it matches the focused profile. Its profile event log records the selection without the name.

Pairing tests use a fictional profile. The public deep-link catalog keeps routing and playback results, with profile labels, household sign-in states and subscription details removed. Benchmark results omit browser capture session IDs and keep frame size, audio and timing measurements. UUIDs inside streaming-service title and episode URLs identify public content. The root license identifies the maintainer by the repository owner's public handle.

All six tracked PNGs serve a documentation link. Two JPEG recipe slides in `docs/images/` illustrate the README examples. They show only the recipe and carry no camera or author metadata. The accessibility guide uses two matching Settings frames, a submenu and a cropped row of app tiles. The browser comparison uses two sheets of synthetic benchmark pages. Visual and PNG metadata inspection found no personal names, profile pickers or lists of paired devices in these files.

Raw deep-link evidence in `spikes/deeplinks/out/`, hardware captures in `spikes/live/results/` and pairing state in `spikes/live/state/` are local and ignored. Keep them out of the public export, including screenshots and JSON referenced by the catalog.

## Private Git history

The table identifies changes found with `git log --all -m -p -S '<private value>'`. Some commits remove an occurrence. Its earlier blob remains reachable from their parents.

| Commit | Paths | Historical data |
| --- | --- | --- |
| `0e05604` | `docs/ARCHITECTURE.md` | TV and Home Assistant LAN addresses |
| `800434b` | `app/config.yaml`, `app/atv_bridge_app/__main__.py`, `app/DOCS.md` | Default TV address in configuration, runtime and instructions |
| `ac44ddb` | `docs/deploy.md`, `docs/plan.html` | LAN addresses, workstation names and network layout |
| `ee64bfe` | `spikes/live/README.md`, `spikes/live/atvlab/common.py` | TV address and absolute path under the workstation user's home |
| `4846a2c`, `4904211` | `spikes/s2-render/results/m4-max.jsonl`, `spikes/s2b-browsers/results/arm64-sender.jsonl` | Transient browser capture session IDs |
| `bdfdb25` | `docs/mcp-api.md` | Tailnet domain and machine name in example URLs |
| `b676652`, `35f7c3d`, `f7c03d1` | `app/config.yaml`, `app/atv_bridge_app/__main__.py`, `app/DOCS.md` | Removal and merge of the household default |
| `e59ce5f`, `f7c03d1`, `5729efa` | `docs/AGENT-GUIDE.md` | HA address in a guide later removed |
| `6f33dec` | `app/atv_bridge_app/data/deeplinks.validated.yaml`, `spikes/deeplinks/candidates.yaml`, `spikes/deeplinks/validate.py`, `docs/deeplinks.md`, `docs/AGENT-GUIDE.md` | Named profiles, service account and subscription observations, hard-coded wake profile |
| `6d887bf` | `docs/SETUP.md` | Addresses of two TVs |
| `76600e5` | `app/tests/test_pair_tools.py` | Real profile labels in fixtures and assertions |
| `5729efa` | `skills/apple-tv/deeplinks.md` | Household subscription details |
| `adde03a` | `LICENSE` | Full maintainer name |

Commit author and committer fields also contain the maintainer's name and GitHub noreply address. Editing files in the working tree does not change these fields.

Gitleaks found five credential-shaped values in the tracked files and their history. All five came from the pyatv import at `db689bd`. The matches were one DMAP example in `vendor/pyatv/docs/development/scan_pair_and_connect.md`, two fake-device commands in `vendor/pyatv/docs/internals/tools.md`, and one fixture key each in `vendor/pyatv/tests/fake_device/{airplay,dmap}.py`. The scans found no household token, pairing record or private key. Upstream examples and third-party attribution remain intact.

## Public history plan

1. Finish the documentation reviews. Recheck setup and deploy examples, the MCP guide, the deep-link guide and skill, and the live spike README for installation addresses, profile names, subscription details and home paths.
2. Assemble the reviewed file set in an empty directory, including this guide and any approved new files. Use the current working-tree contents. Copying the entire checkout would include ignored captures and private Git objects. An archive of the old HEAD would miss uncommitted redactions.
3. Scan that directory with `gitleaks dir --redact .` and the private search terms. Review image pixels and metadata, then run the app and integration tests. Compare the file list to the approved export before initializing Git.
4. Initialize a fresh repository there. Set the intended public author name and email, create one root commit, and verify that `git rev-list --all --count` returns `1`. Push only the new branch to a new, empty GitHub repository. Retain the original checkout and its refs privately.

## App image

The workflow `.github/workflows/app-image.yml` builds the app image on every push to `main`. It can also run by hand through `workflow_dispatch`. Each architecture builds on a native runner: `ubuntu-24.04-arm` for `aarch64` and `ubuntu-latest` for `amd64`. The workflow runs `app/build/stage-context.sh` before it builds `app/Dockerfile`, the same staging step that `push-app` runs. Build layers are cached in the GitHub Actions cache, one scope per architecture.

The workflow pushes these images to GitHub Container Registry:

| Image | Platform |
| --- | --- |
| `ghcr.io/neekolas/aarch64-tv-use-bridge` | `linux/arm64` |
| `ghcr.io/neekolas/amd64-tv-use-bridge` | `linux/amd64` |
| `ghcr.io/neekolas/tv-use-bridge` | Multi-arch manifest of both |

Each push to `main` adds the tags `sha-<7-character commit>`, `latest` and the `version` from `app/config.yaml`. A manual run from another branch adds only the `sha-` tag. The version tag moves to the newest build on every push to `main`, so it does not identify one build until the version changes. Each image carries the OCI labels `org.opencontainers.image.source`, `revision` and `version`.

The workflow uses `GITHUB_TOKEN` and requests `packages: write`. A new package is private by default. Make each package public in its package settings before any host pulls it without credentials.

### Image drift

The Dockerfile runs `apt-get upgrade` and installs apt packages without pinned versions. Two builds of the same commit can therefore contain different Debian packages. The `sha-` tag names the source commit, not a fixed set of packages. The Python base image, tvpipe binary and Python requirements are pinned.

### Pull images in Home Assistant

The Supervisor still builds the app locally. `app/config.yaml` has no `image:` key. With `image:` set, the Supervisor pulls the image and does not build it, and `push-app` development deploys stop working. Make this switch as a separate decision. It would look like this:

```yaml
version: 0.1.3
image: ghcr.io/neekolas/{arch}-tv-use-bridge
```

The Supervisor replaces `{arch}` with the host architecture and pulls the tag that equals `version`. It pulls only when `version` changes. A new image under the same version never reaches an installed app. Bump `version` in `app/config.yaml` in the same push to `main` as the app change, so the workflow publishes that version tag before hosts see the new version.
