# Noter — video research library

A self-hosted, bilingual YouTube research app. Paste a video, playlist, or channel (including a `/posts` link), preserve the original spoken language, and build a searchable archive. The interface switches between English and Chinese.

## Run with Docker

```bash
cp .env.example .env
# Edit .env: set AUTH_TOKEN before making the app accessible remotely.
docker compose up --build -d --remove-orphans
```

Open **http://localhost:8000**. The first image build downloads and verifies the summarization model; the app starts when the inference server reports healthy. Watch progress with `docker compose logs -f inference`. Enter your token in **Settings & setup** if configured.

The stack contains:

- **app:** Python WSGI API, a browser frontend with no build step, a serial processing worker, and a separate interactive discovery worker. Runs as a non-root user with Gunicorn.
- **inference:** CPU-only llama.cpp server running **Qwen2.5 3B Instruct, Q4_K_M**. `Dockerfile.inference` embeds the specific GGUF file (about 2.1 GB), pinned to an upstream revision and checked against its published SHA-256. The container loads `/models/qwen2.5-3b-instruct-q4_k_m.gguf` from its read-only image filesystem, with no runtime model download or model volume. One inference slot and an 8,192-token context keep resource use bounded.

Named volumes preserve SQLite (`library`) and speech models (`speech-models`). `docker compose down` retains them; **`down -v` deletes them**. CPU inference can be slow; start with an 8–16 GB RAM budget and measure actual memory and throughput on your server. These are planning estimates, not benchmarks.

The web port is bound to host loopback. For remote use, place it behind your HTTPS reverse proxy, preserve the incoming Host header, and configure `AUTH_TOKEN`. The inference service is only exposed inside the Compose network. This is a single-user/shared-library app, not a multi-tenant service.

### Building and distributing the embedded-model image

```bash
docker compose build inference
docker compose up -d --build --remove-orphans
```

Build progress shows the model download and checksum verification. Rebuilds reuse the model layer while its inputs and base image remain cached. No host model directory needs to be prepared. Changing the model requires rebuilding the inference image; it is no longer a runtime download setting.

The image includes about 2.1 GB of weights in addition to the CPU runtime. This simplifies deployment rather than eliminating those bytes. Allow extra disk space for Docker's build cache. Containers sharing the same image reuse its immutable layers. The Qwen model license is included at `/models/LICENSE.Qwen`.

To transfer the built image without a model volume:

```bash
docker image save yt-summarizer-inference:qwen2.5-3b-q4_k_m -o inference.tar
# On the destination host (same CPU architecture):
docker image load -i inference.tar
```

Alternatively, set `INFERENCE_IMAGE` to your registry tag before building and push that image. On the destination, also supply the app image/configuration and start the stack without rebuilding the inference service. The image can run standalone with `docker run --rm --read-only --tmpfs /tmp -p 127.0.0.1:8080:8080 yt-summarizer-inference:qwen2.5-3b-q4_k_m`.

### Upgrading from the earlier Ollama setup

The startup command removes the obsolete Compose service containers, but does not delete their volumes. Existing `library` and `speech-models` volumes continue to be used. Old `OLLAMA_*` environment settings are no longer used; update them using `.env.example` without overwriting your access token. Old `ollama-models` and `llm-models` volumes are left untouched and are no longer used. The custom inference image contains its own GGUF file. Existing saved summaries retain their original model attribution.

## Discovery, backfill, and ongoing monitoring

1. Paste a source and click **Find videos** (or press Enter) to queue a YouTube lookup. Pasting alone does not fetch videos. Discovery does not save or subscribe to the source. The status below the input shows queued/running progress or an error. Results appear once publication-date lookups finish; large pages can take several minutes. A feed becomes monitored only when you explicitly save selected video links to Library.
2. In **Source results**, choose **Manually**, **Last N videos**, or **Last N days**. A single **Number (N)** field sits to the right: disabled in manual mode and enabled for either automatic mode. N must be a positive whole number. Last N days uses the preceding N × 24 hours, with a fixed cutoff while additional pages load.
3. Last-N-days, last-N-videos, and manual selection operate on loaded results. The preview scans 25–500 entries per page. Use **Load more videos** to append older entries; time-based selection includes newly loaded matching videos. Metadata failures remain visible with unknown dates and expandable error details; unknown dates are excluded from time-based selection but can be selected manually.
4. Click **Summarize** at the top of the results to process the non-empty selection. Completed results can be opened using **View summary**. If their links are already saved, results appear in Library automatically; otherwise results remain drafts until saved.
5. **Add to Library** is available whenever videos are selected, regardless of summarization status. It immediately saves their canonical video links and available metadata. Existing or later transcripts and summaries attach to those same records. Saving automatically queues summarization for selected videos without results, reuses queued/running jobs, and requeues failed attempts. Saving and queue insertion are atomic. Saving a feed also starts or resumes new-upload monitoring from now onward. Saving an individual video does not create a feed subscription. Repeated saves are idempotent.
6. Changing selection alone never queues or saves videos. Processing drafts and jobs are persisted internally for restart recovery, but excluded from Library searches and counts. Existing library records are preserved by a one-time schema migration.
7. **Followed feeds** shows monitoring progress and lets you pause/resume. Pausing stops subsequent scans; already queued or running processing finishes. Saved checkpoints allow monitoring to catch up after downtime. Existing subscriptions with an older backfill boundary retain that boundary.


Checks are queued every `FEED_POLL_MINUTES` (15 by default, minimum 5). The serial processing worker keeps CPU use predictable; interactive Find videos lookups run on a separate worker and show persistent progress below the link field; an existing backlog can delay a check. Failed discovery pages retry at the next interval without advancing the checkpoint. Individual failed transcriptions/summaries can be retried from the queue. No duplicated automatic processing for already-seen videos; existing transcripts are reused on summary retries.

Channel `/posts`, `/streams`, and `/shorts` links normalize to the channel's **video uploads**. Text-only community posts and dedicated Shorts/live feeds are not ingested. Channel paging assumes YouTube's newest-first upload ordering and overlaps pages; playlists are scanned to the end because their ordering can be arbitrary. Deleted/private videos, unavailable dates, YouTube rate limits, or account restrictions can interrupt backfill; readable entries are queued, and errors are shown instead of silently advancing past failed metadata. When YouTube supplies only an upload date, it is stored as midnight UTC with `date_precision: day`; intraday filters cannot recover an unknown publication time. Automatic monitoring includes the whole boundary day for day-only dates so same-day new uploads are not lost.

## Transcripts and summaries

The app selects original caption tracks, excludes any track with a `tlang` translation parameter, and uses original auto-caption metadata ahead of localized language metadata. If the original language cannot be established or captions are unavailable, faster-whisper detects language from audio using `task=transcribe`, never `translate`. The audio fallback explicitly selects original-language audio when multiple dubbed tracks exist, and rejects ambiguous multilingual tracks. CPU uses INT8; a configured GPU failure falls back to CPU. The first speech transcription downloads its model into the cache.

llama.cpp summarizes bounded transcript chunks and combines all section summaries hierarchically. It is instructed to preserve the original language and script. A script sanity check rejects clearly translated Chinese/English output; it is not a complete language or factual verifier. Small models can still misclassify topics and recommendations. Transcripts are committed **before** summarization so model failures do not lose source text.

Ordinary YouTube playback ads generally are not part of downloaded source media. Creator-read sponsors remain in the original transcript; prompts exclude their pitches from the main summary. No SponsorBlock cutting is enabled, preserving original timestamps. YouTube delivery changes, including server-side ad experiments, may still require yt-dlp updates.

## Search and stock research

Search Chinese/English transcript and summary text, or filter by exact ticker, creator, category, and publication dates. Text search preserves literal substring matching using SQLite FTS5 trigram indexes for queries of three or more characters. One- and two-character queries use an indexed character/bigram lookup, including short Chinese terms. Only index-matched candidates undergo literal text verification; structured fields are also indexed. Results are capped at 1,000 per query—narrow dates for large archives.

Stock mentions store the symbol, model-classified stance, exact source quote, timestamp, and optional source-supported target/currency/horizon fields. Symbols must literally appear in the evidence segment; company-to-ticker inference is intentionally absent. A mention is not necessarily a recommendation. Historical return calculations, corporate-action-adjusted prices, exchange normalization, OCR, and recommendation verification are **not implemented**. The schema retains provenance and extension JSON so those features can be added later.

Markdown exports contain summaries, stock mentions, and timestamped transcript links. Table/chart capture is not included. Audio downloads are limited to 1 GB per file, and temporary files are removed after the job.

## Configuration and operations

| Variable | Default | Purpose |
| --- | --- | --- |
| `LLM_MODEL` | `qwen2.5-3b-instruct-q4_k_m` | Served API model alias; the embedded GGUF is fixed in `Dockerfile.inference`. |
| `LLAMA_IMAGE` | `ghcr.io/ggml-org/llama.cpp:server` | Upstream CPU base image used at build time; pin a tested digest for repeatable releases. |
| `INFERENCE_IMAGE` | `yt-summarizer-inference:qwen2.5-3b-q4_k_m` | Tag for the custom image containing the model. |
| `WHISPER_MODEL` | `small` | Multilingual speech model; do not use English-only `.en` variants for Chinese. |
| `FEED_POLL_MINUTES` | `15` | Minimum interval between completed monitoring scans. |
| `AUTH_TOKEN` | empty | Shared API bearer token; browser stores it only in session storage. |
| `PORT` | `8000` | Loopback web port. |
| `DATA_DIR` | `./data` locally | SQLite storage; `/data` in Docker. |
| `LLM_URL` | `http://127.0.0.1:8080` locally | llama-server base URL without `/v1`; Compose uses `http://inference:8080`. |
| `YOUTUBE_COOKIES` | unset | Optional server-side Netscape cookie file for restricted YouTube sessions. |

For cookies, mount a private cookie file into the app container with write access for UID 10001 (yt-dlp updates its cookie jar), then set `YOUTUBE_COOKIES` to that path using a Compose override. Never commit it or serve it through the app.

`/healthz` checks HTTP availability; **Settings** checks installed tools and the served model alias. These do not guarantee YouTube access or downloadable speech weights. Use `docker compose logs -f app` to diagnose runtime failures. Update yt-dlp by rebuilding with `docker compose build --pull --no-cache app`; YouTube compatibility changes frequently. Dependency ranges and moving image tags are supplied for bootstrap, not a frozen production release: pin validated versions/digests after acceptance testing.

Back up the `library` volume while the app is stopped, or use SQLite's online backup API. Retain `library.sqlite3-wal` with the database if copying a live volume without the backup API. Keep one app replica and **one Gunicorn worker process**; multiple processes would compete for the embedded scheduler. Larger deployments should move scheduling to a dedicated worker service and storage to PostgreSQL.

## Local container development with automatic reload

Run `make dev`, then open **http://localhost:8000**. Python edits restart only the app container, dependency changes rebuild it, and frontend edits appear after a browser refresh. The inference container stays running.

See [development.md](development.md) for the complete workflow, the file-by-file reload/rebuild table, manual rebuild commands, persistent-data behavior, and returning to production mode.

## Local development and validation

Python 3.12+, Node 22+ (yt-dlp's JavaScript challenge solver), ffmpeg, and llama.cpp are required for live media processing. The HTTP app and offline tests need only Python's standard library.

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
# Run this in a separate terminal; it stays running:
llama-server -hf Qwen/Qwen2.5-3B-Instruct-GGUF \
  -hff qwen2.5-3b-instruct-q4_k_m.gguf \
  --alias qwen2.5-3b-instruct-q4_k_m -c 8192 -np 1 -ngl 0 --port 8080
# In the app terminal:
python3 -m app.server

python3 -m unittest discover -s tests -v
node --check static/app.js
node --test tests/frontend.test.cjs
docker compose config --quiet
```

Tests use temporary SQLite databases and mocked media/model adapters. They cover language selection, timestamp evidence, request validation, restart recovery, persistent monitoring and backfill, exports, and UI selection/rendering helpers. They do not replace an end-to-end deployment test with a real YouTube video and downloaded models.

Implementation: `app/media.py` handles YouTube/ASR; `app/summarize.py` handles inference and validation; `app/db.py` owns storage; `app/feeds.py` owns subscriptions; `app/worker.py` runs persistent jobs; `app/server.py` serves the API; `static/` holds the UI. SQLite stores indexed video metadata plus raw metadata JSON, transcript segments, structured summaries, source-backed mentions, jobs, and feed checkpoints. Version markers prepare for future explicit schema migrations.

Upstream references: [yt-dlp JavaScript runtime requirements](https://github.com/yt-dlp/yt-dlp/wiki/EJS), [faster-whisper CPU inference](https://github.com/SYSTRAN/faster-whisper), [llama.cpp Docker](https://github.com/ggml-org/llama.cpp/blob/master/docs/docker.md), [llama-server API](https://github.com/ggml-org/llama.cpp/tree/master/tools/server).

Library groups saved videos by their saved feed/channel link; standalone video links appear under **Ungrouped**. Videos saved through multiple feeds appear in each associated group. Search filters apply within groups.

- **Delete feed / stop watching** stops monitoring and cancels queued scans. Saved summaries and their grouping remain; already queued video processing can finish. Monitoring can be resumed from Followed feeds.
- **Delete summary** removes that video's summary, extracted stock mentions, and Library membership across all groups. The transcript remains for reuse. Feed discovery history is retained so monitoring does not automatically recreate the deleted summary. Wait for active processing to finish before deleting; explicitly summarize and save again to restore it.

Video identity uses the YouTube video ID and canonical `https://www.youtube.com/watch?v=VIDEO_ID` URL. Short links, timestamped links, and feed entries share one transcript and summary. Saving through multiple sources adds associations, not copies. Ordinary summarization reuses existing results and active jobs; only **Generate / retry summary** explicitly regenerates an existing summary. Deleting a shared summary removes it from every group.

The **Processing queue** shows every active job, followed by up to 100 recent results. Video titles, creators, current stages (including summary section counts), waiting positions, time since enqueue, and time since the last progress update refresh every five seconds. Positions are separate for interactive discovery and the shared video/feed worker. A queue banner remains available across views; refresh failures mark status as potentially stale. Stage-update age is not a completion estimate—CPU transcription or an inference request can take time without reporting new progress.

For debugging without an app container, use `make local-setup` and `make local`. See [native development](development.md#run-the-app-directly-recommended-for-debugging) for preserving the Docker library, exposing the existing inference service, and required host dependencies.

Channel monitoring uses YouTube's public Atom feed once the channel ID is resolved. Routine checks default to 15 minutes; full yt-dlp reconciliation runs every 6 hours (`FEED_RECONCILE_HOURS`). Initial backfills, unfinished pages, feed failures, and gaps of at least an hour (or three configured polling intervals, whichever is larger) use listing scans. RSS checks never advance the historical listing checkpoint. Playlists and channels without a resolved channel ID continue using yt-dlp. Both paths deduplicate by video ID and retain already-seen history. Existing explicit polling settings remain effective; set `FEED_POLL_MINUTES=15` to adopt the new interval.

Search schema migration 5 builds indexes for existing Library entries on startup. Inserts, transcript/summary replacements, and Library removal maintain the indexes in the same transaction. No external search service or Python package is required; SQLite must support FTS5 with its trigram tokenizer. Indexes use additional disk space and initial migration takes time proportional to saved content. Common one-character searches can still match many videos; use longer text or filters for selective results.

### Configure the LLM in the app

Click the gear beside **Noter** in the left navigation. Enter an API base URL, model name/alias, and optional bearer API key. A host-only URL such as `http://127.0.0.1:8080` automatically uses `/v1`; for providers with a custom API path, include the full base path (for example `https://provider.example/v1`). Choose local llama.cpp schema mode, compatible JSON mode, or prompt-only JSON according to the server's capabilities. Providers must implement OpenAI-compatible Chat Completions; native non-compatible APIs need an adapter.

**Test connection** checks `/models` without saving or generating text. Some providers do not implement model listing, so test failure does not necessarily mean their chat endpoint is unavailable. Save applies to subsequent summaries; an ongoing summary keeps one configuration snapshot and records its actual model. Existing summaries are not automatically regenerated. Public endpoints receive the transcript text used for summarization.

Settings override environment defaults and persist in `DATA_DIR/llm-settings.json` with owner-only file permissions. The API key is stored on the server, not returned by the settings API or stored in browser storage. Leave the key blank to retain it, or select **Remove saved API key**. Configuration uses the app's existing access-token protection; use that protection when exposing a shared server.
