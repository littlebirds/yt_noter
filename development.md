# Local Development

## Run the app directly (recommended for debugging)

The Python backend and its discovery/summarization workers run on the host. Docker remains available for production. You may reuse the existing llama.cpp container for inference; no app container is needed.

Requirements: Python 3.11+, Make, ffmpeg, and Node.js 22+. On Debian/Ubuntu, install `python3-venv` and `ffmpeg` with your package manager and ensure a supported Node.js is on PATH. Native dependencies are installed in `.venv`:

```bash
make local-setup
# Stop Compose Watch with Ctrl+C first if make dev is running.
docker compose stop app
make local-inference
make local
```

Open http://127.0.0.1:8000. Logs appear in the terminal. Ctrl+C stops the backend and its child processes. `make local-run` starts without a watcher for debugging. Saving Python files restarts the whole backend, including workers; interrupted jobs resume from persisted state. Static HTML/CSS/JS edits need only a browser refresh. After dependency changes, stop the app, run `make local-setup`, then restart. No app image rebuild is needed.

### Keep the existing Docker library

Native mode defaults to `./data/library.sqlite3`; this is **separate from the Docker library volume**. To continue with the existing library, stop the app container before copying its data (including any SQLite WAL files). The commands below create a separate directory, preserving any existing local data:

```bash
docker compose stop app
library_copy=$(mktemp -d "$PWD/data/docker-library-XXXXXXXX")
app_container=$(docker compose ps -a -q app)
docker cp "$app_container:/data/." "$library_copy/"
export DATA_DIR="$library_copy"
make local
```

Keep this `DATA_DIR` path for subsequent runs. If Docker preserved a different file owner, change ownership of this copied directory to your host user. The Docker volume stays untouched; this is a one-time copy, not synchronization. Do not run two backend processes against the same database.

### Inference and configuration

`make local-inference` exposes the existing llama.cpp service only at `127.0.0.1:8080`. It can recreate the inference container to add the port; it does not rebuild the model image. Native defaults already use this address and the existing Qwen 3B alias. Set `LLM_URL` if using a different port or a native `llama-server`. If inference is stopped, browsing and saving links still work; summaries require it.

Native mode reads exported environment variables, **not `.env` automatically**. Set `DATA_DIR`, `PORT`, `AUTH_TOKEN`, `LLM_URL`, `WHISPER_MODEL`, or `YOUTUBE_COOKIES` before starting as needed. Host Whisper downloads its own cache on first use; Docker's speech cache remains untouched.

To inspect the failing video directly:

```bash
.venv/bin/python -m app.diagnose 'https://www.youtube.com/watch?v=M6ipkb3_Ivk'
```

## Docker development workflow (alternative)

## Start the development stack

Requirements: Docker with Compose 2.32 or newer, and Make. Run commands from the repository root.

```bash
# First-time configuration only; preserve an existing .env file.
test -f .env || cp .env.example .env
make dev
```

Open **http://localhost:8000** (or the port configured in `.env`). Leave `make dev` running: its foreground Compose Watch process handles automatic restarts and rebuilds.

The command starts or reuses the inference container, then builds/starts the app and watches its files. The inference image contains Qwen2.5 3B weights; its first build downloads those weights. Ordinary app edits do not rebuild that image or reload the model.

The development override mounts `app/` and `static/` from your working tree into the app container, read-only. Changes on the host are visible inside the container immediately, but Python must restart to execute changed modules. The browser must refresh to load changed frontend files.

## When changes take effect

This table applies while **`make dev` is running**.

| Changed files | What happens automatically | What you need to do | Image rebuild? |
| --- | --- | --- | --- |
| `static/index.html`, `static/style.css`, `static/app.js` | The server reads the updated files on the next request. | Refresh the browser. There is no browser auto-refresh or hot module replacement. | No |
| Python files under `app/` | Compose Watch restarts the entire app container; new requests use the updated code after startup. | Wait for the restart; refresh the browser if displaying old results. | No |
| `requirements.txt` | Compose Watch rebuilds the app image and replaces the app container. | Wait for installation/build/startup to finish. | App only, automatic |
| App `Dockerfile` | Compose Watch rebuilds the app image and replaces the app container. | Wait for the build/startup to finish. | App only, automatic |
| `.env`, `compose.yaml`, `compose.dev.yaml` | Nothing; these files are not watched. | Stop the watch session with Ctrl+C, then run `make dev` again. Compose recreates services whose configuration changed. | Usually no; build-related changes may require an explicit build. |
| `Dockerfile.inference` or its model revision/checksum | Nothing; inference is not watched. | Explicitly rebuild and recreate inference using the commands below. | Inference only, manual |
| `LLAMA_IMAGE` in `.env` | Nothing; this is an inference build argument. | Explicitly rebuild and recreate inference. | Inference only, manual |
| `INFERENCE_IMAGE` in `.env` | Nothing; this changes the output image tag. | Build the new tag and recreate inference. | Inference only, manual |
| Tests, Markdown documentation, `AGENTS.md` | No runtime action. | Run relevant tests or read the updated documentation. | No |
| `Makefile` | The running command is unaffected. | Stop the session and invoke the updated Make target. | No, unless the target requests one |

`__pycache__/` and `*.pyc` changes are ignored by the Python watcher. Editing files outside the mounted/watched paths does not automatically update the container.

A restart reloads code; it does not regenerate existing saved summaries. Use **Generate / retry summary** on a saved video to exercise changed summarization logic. Database changes still require explicit migrations; rebuilding an image does not reset or migrate data by itself.

## Explicit app rebuild and logs

```bash
make dev-rebuild  # Rebuild/recreate only the app; leave inference running.
make dev-logs     # Follow app logs in another terminal.
make dev-stop     # Stop only the app; preserve inference and volumes.
```

`make dev-rebuild` starts the app in the background but does **not** start a new watch session. Use `make dev` when you want automatic restarts/rebuilds. Without a running watcher, static changes still appear on browser refresh because they are mounted; Python changes require an app restart:

```bash
docker compose -f compose.yaml -f compose.dev.yaml restart app
```

A plain `restart` does not apply changed environment variables or Compose configuration. Re-run `make dev` to apply those changes.

## Rebuild inference only when needed

Stop the watch session before changing inference configuration, then run:

```bash
docker compose -f compose.yaml -f compose.dev.yaml build inference
docker compose -f compose.yaml -f compose.dev.yaml up -d --no-deps inference
make dev
```

This replaces the model server and temporarily interrupts inference. The model download layer is reused if its inputs and base image remain cached. A new model requires updating the download revision/checksum and model path as appropriate; changing `LLM_MODEL` alone only changes the API alias, not the embedded weights.

## Jobs and persistent data

The whole app container restarts so the old background worker exits before the new worker resumes interrupted jobs. A running transcription or summary can restart from its last saved stage. Saved transcripts, summaries, subscriptions, and queued jobs remain in the `library` volume. Speech model downloads remain in `speech-models`.

Development and production use the **same volumes** with these commands. Development processing therefore updates the same research library. Do not use `docker compose down -v` as a rebuild step: it deletes the declared data volumes.

Ctrl+C stops the foreground development session; inference was started separately in the background and remains running.

## Return to production mode

Stop the development watch session, then run:

```bash
docker compose -f compose.yaml up --build -d --no-deps app
```

Production has no source bind mounts or file watching. Python and frontend changes both require rebuilding/recreating the app image to become available. Refresh the browser after deployment to load updated frontend code. If starting the entire stack from scratch, omit `--no-deps app`.

## Without Make

These commands are equivalent to `make dev`:

```bash
docker compose -f compose.yaml -f compose.dev.yaml up -d inference
docker compose -f compose.yaml -f compose.dev.yaml up --build --no-deps --watch app
```

## Validation and troubleshooting

```bash
python3 -m unittest discover -s tests -v
node tests/frontend.test.cjs
node --check static/app.js
docker compose -f compose.yaml -f compose.dev.yaml config --quiet
```

- **Changes not showing:** check that the development override is active and the watch terminal is still running. Refresh the browser for frontend changes.
- **App restarting repeatedly:** inspect `make dev-logs` for syntax/import errors.
- **Docker permission errors:** run commands from a terminal with access to your Docker daemon. Configuration validation alone does not prove containers can start.
- **Inference unavailable:** inspect `docker compose logs -f inference`; the app waits for inference health on a normal startup. Live model/YouTube behavior requires runtime testing in addition to the offline tests above.

### Diagnose a video's audio selection

Run `docker compose exec -T app python -m app.diagnose 'https://www.youtube.com/watch?v=VIDEO_ID'` after the app has loaded the current code. The command reads the latest job state, reports the installed yt-dlp version, fetches metadata, and lists audio language labels and the selected format. It does not download audio, enqueue work, or change the library. Signed media URLs and cookies are excluded from the output.

## Search backend boundary

`app/search.py` defines the `SearchBackend` contract and selects the current adapter. `app/search_sqlite.py` owns FTS5/short-term indexes, search schema migration, filtering, limits, and result ordering. `app/db.py` remains the authoritative store and loads the ordered video IDs returned by search; the HTTP endpoint and UI do not depend on SQLite search syntax.

Adapters implement:

- `initialize(transaction)`: initialize storage and backfill saved documents.
- `sync(transaction, video_id)`: reflect updated content, metadata, stock mentions, or Library membership, including deletion.
- `find(transaction, filters, limit)`: return unique saved video IDs in display order, applying filters before the limit. Filters are `q`, `ticker`, `creator`, `category`, `language`, `from`, and `to`.

To add Elasticsearch, implement another adapter and select it in `app/search.py`. SQLite remains the content store. A remote adapter must write durable outbox events during `sync`, then deliver them after the database transaction commits; network calls here cannot provide atomic updates across SQLite and Elasticsearch. That outbox/delivery worker is future work, not part of the current SQLite adapter. Define and test the replacement's substring/Chinese matching semantics, backfill, retry policy, and indexing lag explicitly. Library hydration drops IDs no longer saved locally and preserves adapter result order.
