# Repository Guidelines

## Project Structure & Module Organization

`app/` contains the Python backend: `server.py` provides the WSGI API, `db.py` manages SQLite, `media.py` handles YouTube and transcription, `summarize.py` integrates Ollama, and `feeds.py`/`worker.py` manage monitoring and durable jobs. `static/` contains the bilingual HTML, CSS, and JavaScript interface. Tests live in `tests/`. Runtime databases, images, and model caches are generated data, not source assets.

## Build, Test, and Development Commands

- `docker compose up --build -d`: build and start the production stack with persistent volumes and local models.
- `python3 -m app.server`: run the local development server on port 8000.
- `python3 -m unittest discover -s tests -v`: run backend tests without external services.
- `node --test tests/frontend.test.cjs`: check frontend selection and rendering helpers.
- `node --check static/app.js`: validate JavaScript syntax.
- `docker compose config --quiet`: validate deployment configuration.

See `README.md` for dependency installation, authentication, and model configuration.

## Coding Style & Naming Conventions

Use four-space indentation and snake_case for Python, and descriptive camelCase names for JavaScript. Keep SQL parameterized and transactions short. No formatter or linter is currently configured; follow existing style and avoid unrelated reformatting. Keep interface strings in the English/Chinese translation dictionaries.

## Testing Guidelines

Use Python `unittest` and Node's built-in test runner. Name Python tests `test_*.py` and frontend tests `*.test.cjs`. Use temporary databases and mocked external adapters. Cover checkpoint recovery, language preservation, input validation, and source evidence when changing these behaviors. No coverage percentage is enforced.

## Commit & Pull Request Guidelines

No readable Git history established a commit convention. Use focused, imperative subjects such as `Add feed checkpoint recovery`. Pull requests should explain behavior changes, list validation, and document configuration or schema changes. Include screenshots for UI changes and link relevant issues.

## Operational Constraints

Run one application worker process. Preserve original-language transcripts and original-video timestamps. Never commit credentials, cookies, generated media, or databases. Extend storage through explicit migrations without discarding existing research.
