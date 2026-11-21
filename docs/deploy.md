# Deploy from a workstation

Use this route to install the bridge app and custom integration on Home Assistant OS. Run the commands from the repository root. The workstation needs `uv`, `rsync`, `ssh`, `scp` and `ssh-keygen`. The deployer requests Python 3.13 and declares its dependencies inline. The HA host needs internet access for the build. [TV and host requirements](SETUP.md#what-you-need)

## Set the origin and token

Use the same scheme, host and port you use to open HA. The deployer defaults to `http://homeassistant.local` on port 80. The examples explicitly select HA’s usual local port, 8123. Change this value for your installation. Supply an origin without an API path, credentials, query or fragment.

```sh
HA_URL='http://homeassistant.local:8123'
```

Run this from a trusted network. An HTTP origin carries the admin token in plain text. For HTTPS, the origin’s host must still reach the Terminal & SSH port and the plain HTTP watchdog on port 8766. A remote web proxy alone cannot supply those connections.

In an HA administrator’s profile, select **Security → Long-lived access tokens → Create token**. Copy the token. Store it as one line in the regular file `~/.config/atv-bridge/ha_token` with mode `600`. For example, prepare the file, open it in your editor and paste the token:

```sh
mkdir -p "$HOME/.config/atv-bridge"
touch "$HOME/.config/atv-bridge/ha_token"
chmod 600 "$HOME/.config/atv-bridge/ha_token"
vi "$HOME/.config/atv-bridge/ha_token"
```

## Install in order

1. **Check the host.**

   ```sh
   uv run tools/deploy/deploy.py --url "$HA_URL" check
   ```

   Check the Core and Supervisor versions, architecture, free disk, network interfaces and installed apps. For installed SSH apps, the full check includes the mapped port and protection setting. Use `check --limited` for the API, Supervisor info and installed-app inventory only.
2. **Set up Terminal & SSH.**

   ```sh
   uv run tools/deploy/deploy.py --url "$HA_URL" ssh-setup
   ```

   This installs the official app if needed, preserves other authorized keys, disables TCP forwarding and verifies key-based login as `root` in the app container. It uses `~/.ssh/atv_bridge_ha` if present, otherwise an existing `~/.ssh/id_ed25519` key pair. If neither is available, it creates a dedicated Ed25519 key without a passphrase. Load a passphrase-protected identity into your SSH agent first.

   The deployer reuses a running SSH app’s mapped port. Otherwise it checks an existing mapping first, then ports 2222 through 2253. It reports the selected port. OpenSSH accepts a new host key on first contact and rejects a changed key.
3. **Build and start the app.**

   ```sh
   uv run tools/deploy/deploy.py --url "$HA_URL" push-app
   ```

   The command runs `app/build/stage-context.sh` to copy the vendored pyatv fork into `app/vendor/pyatv`. It replaces the app source at `/local_apps/atv_bridge` or `/addons/atv_bridge`, reloads the store and installs `local_atv_bridge`. On later deploys, it updates a changed store version or rebuilds the same version. A rebuild interrupts the running app. Pairings live in the app data directory.

   The Supervisor builds the image on the HA host. GitHub Actions also publishes app images to GitHub Container Registry on every push to `main`, but the app does not pull them. [App image](PUBLISHING.md#app-image)

   Discovery is the default. Optional flags are `--apple-tv-host` with an IPv4 address, `--log-level` with `debug`, `info`, `warning` or `error`, and `--presentation-mode` with `mirror`, `play_url` or `auto`. An omitted flag keeps the installed option or the new install’s default. Set the receiver list, profiles, mirror capacity, resolution and frame rate on the app’s **Configuration** tab. [App options](SETUP.md#app-options)

   The 15-minute deadline starts at store reload. The deployer follows logs while waiting for `http://<HA host>:8766/api/health` to return `"ok":true` and `"state":"running"`. This works before pairing. If the deadline expires or a reply is lost, inspect app status and Supervisor jobs before retrying. The build may still be running.
4. **Install the integration and restart Core.**

   ```sh
   uv run tools/deploy/deploy.py --url "$HA_URL" push-integration
   ```

   This replaces `custom_components/atv_bridge` under the SSH app’s `/homeassistant` or `/config` mount, checks the HA configuration, requests a Core restart and waits for `RUNNING`. Restarting Core briefly interrupts automations. If the config check fails, the new source is already on disk. Correct it or redeploy the prior source before restarting. An interrupted upload reports its staging directory.
5. **Disable an existing built-in entry for each TV you are moving.** Skip this step for a TV with no built-in `apple_tv` entry. Use its exact entry title from **Settings → Devices & services → Apple TV**. Preview the match before applying it.

   ```sh
   ENTRY_TITLE='<built-in Apple TV entry title>'
   uv run tools/deploy/deploy.py --url "$HA_URL" disable-builtin --entry-title "$ENTRY_TITLE"
   uv run tools/deploy/deploy.py --url "$HA_URL" disable-builtin --entry-title "$ENTRY_TITLE" --yes
   ```

   The command requires one exact title match in the `apple_tv` domain. The first call only reports it. The second disables it and checks the result. Restart Core from the UI if HA requests it. To roll back, stop the bridge app and re-enable the built-in entry.
6. **Pair the TVs and verify agent access.** Configure the discovered bridge in **Settings → Devices & services**. If discovery is missing after Core restarts, run the `restart-app` command below. Enter the AirPlay PIN shown on the selected TV. For screen access, open **Remote App and Devices** on that TV before requesting its six-digit PIN. Use **Add Apple TV** on the existing entry for each additional receiver. Connect your client and check a targeted `tv_status`. [Pair the first TV](SETUP.md#pair-the-first-tv) · [Connect an agent](SETUP.md#connect-an-agent)

## Operations

```sh
uv run tools/deploy/deploy.py --url "$HA_URL" status
uv run tools/deploy/deploy.py --url "$HA_URL" logs
uv run tools/deploy/deploy.py --url "$HA_URL" logs --follow
```

`status` reports the app version, state and watchdog result. Use the agent tool `tv_status` for receiver health. Press Ctrl-C to stop following logs. To interrupt and restart the app, run:

```sh
uv run tools/deploy/deploy.py --url "$HA_URL" restart-app
```

Run the deployer’s local tests without deploying:

```sh
uv run --isolated --python 3.13 --with pytest --with aiohttp pytest tools/deploy
```
