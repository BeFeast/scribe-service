# bgutil PO-token provider sidecar

Layer A of the download anti-bot strategy (#307, #309). The sidecar is the
[`brainicism/bgutil-ytdlp-pot-provider`](https://github.com/Brainicism/bgutil-ytdlp-pot-provider)
HTTP server. The yt-dlp plugin `bgutil-ytdlp-pot-provider` is installed in the
scribe image via `pyproject.toml`; yt-dlp auto-discovers it and asks
the sidecar for GVS PO tokens whenever a youtube extraction needs one.

Without this, yt-dlp logs:

```
WARNING: [youtube] <id>: mweb client https formats require a GVS PO Token which was not provided
```

…and falls back to clients that trip YouTube's bot wall.

## Topology

- Service name: `scribe-pot`
- Image: `brainicism/bgutil-ytdlp-pot-provider:1.3.1`
- Port: `4416/tcp` (in-network only — not published to the host)
- Network: the implicit `scribe_default` project network shared with the
  `scribe` container (subnet pinned in `compose.yaml`)
- Settings field: `bgutil_pot_base_url` (env `SCRIBE_BGUTIL_POT_BASE_URL`)

The `scribe` container forwards the URL to yt-dlp as
`--extractor-args "youtubepot-bgutilhttp:base_url=$SCRIBE_BGUTIL_POT_BASE_URL"`.
Setting the env var to empty disables the integration without removing the
plugin.

## Deploy lifecycle (Dockhand-adopted stack)

The devbox `/opt/stacks/scribe` stack is adopted by Dockhand; do **not** run
`docker compose up` against it. To roll out the sidecar:

1. Pull workshop `main` into `/opt/stacks/scribe/src` (already covered by the
   normal sync).
2. Update `/opt/stacks/scribe/compose.yaml` to include the `scribe-pot`
   service block and the new env var on `scribe` (mirrors the stub in the
   repo `compose.yaml`).
3. Reconcile via Dockhand (http://10.10.0.13:13090/stacks → scribe → Apply)
   or the equivalent `dockhand` CLI verb. The image is pulled, the sidecar
   starts, and scribe is recreated with the new env.
4. Verify (see below).

## Verification

From inside the `scribe` container:

```sh
docker exec scribe sh -c 'curl -fsS "$SCRIBE_BGUTIL_POT_BASE_URL/ping"'
```

A green canary is the simplest end-to-end check:

```sh
docker exec scribe sh -c 'uv run python -m scribe.worker.download_canary'
```

`yt-dlp -v <url>` (run inside the container) should now list the bgutil
provider in its PO-token providers, and the GVS PO-token / EJS warnings
should be absent from stderr.

## Rollback

Set `SCRIBE_BGUTIL_POT_BASE_URL=` (empty) in the stack `.env` (or remove the
env var) and reconcile. The plugin stays installed but is skipped; yt-dlp
reverts to the pre-#309 behaviour. The sidecar may be left running or
removed.
