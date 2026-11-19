"""WSGI API and static frontend. Production runs one Gunicorn worker with threads."""
import hmac
import importlib.util
import json
import logging
import mimetypes
import os
import re
import threading
from datetime import date
from urllib.parse import parse_qs
from urllib.request import Request, urlopen
from . import config, db, media, worker, feeds, llm_settings

TOKEN = os.getenv('AUTH_TOKEN', '')
MAX_BODY = 2_000_000


def valid_id(value):
    if not isinstance(value, str) or not media.VIDEO_ID.fullmatch(value):
        raise ValueError('Invalid video ID.')
    return value


def integer(value, low, high):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f'Expected an integer from {low} to {high}.')
    return value


def export_markdown(video):
    lines = [f"# {video['title']}", '', f"Source: {video['url']}",
             f"Creator: {video['creator']}", f"Published: {video['published_at'] or 'Unknown'}",
             f"Language: {video['language'] or 'Unknown'}", '', '## Summary', '',
             video['summary']['markdown'] if video['summary'] else 'Summary not generated.']
    if video['mentions']:
        lines.extend(['', '## Stock mentions (model-extracted; verify against source)', ''])
        for m in video['mentions']:
            lines.extend([f"- **{m['ticker']}** · {m['stance']} · [{m['timestamp']:.0f}s]({video['url']}&t={int(m['timestamp'])}s)",
                          f"  > {m['evidence']}"])
    if video['transcript']:
        lines.extend(['', '## Transcript', ''])
        for segment in video['transcript']['segments']:
            lines.append(f"[{segment['start']:.0f}s]({video['url']}&t={int(segment['start'])}s) {segment['text']}")
    return '\n'.join(lines)


def api(method, path, query, body):
    if path == '/api/llm-settings':
        if method == 'GET':
            return llm_settings.public()
        if method == 'POST':
            return llm_settings.save(body)
    if method == 'POST' and path == '/api/llm-settings/test':
        settings = llm_settings.validate(body, llm_settings.load())
        try:
            with urlopen(Request(llm_settings.api_url(settings, 'models'), headers=llm_settings.headers(settings)), timeout=10) as response:
                models = [m['id'] for m in json.load(response).get('data', [])]
            return {'models': models, 'model_ready': settings['model'] in models}
        except Exception as exc:
            code = getattr(exc, 'code', None)
            raise ValueError(f'Connection test failed (HTTP {code}). Check endpoint and key.' if code else 'Connection test failed. Check endpoint and service availability.') from None
    if method == 'GET' and path == '/api/feeds':
        return {'feeds': feeds.sources(), 'poll_minutes': feeds.POLL_MINUTES}
    if method == 'POST' and path == '/api/feeds':
        feeds.update_source(str(body.get('id', '')), body.get('enabled'))
        return {'ok': True}
    if method == 'POST' and path == '/api/library/delete-summary':
        return db.delete_summary(valid_id(body.get('id')))
    if method == 'GET' and path == '/api/library':
        for key in ('from', 'to'):
            if query.get(key):
                date.fromisoformat(query[key])
        return db.library(query)
    if method == 'GET' and path.startswith('/api/jobs/'):
        with db.connect() as con:
            row = con.execute('SELECT * FROM jobs WHERE id=?', (path.removeprefix('/api/jobs/'),)).fetchone()
        if not row:
            raise LookupError('Job not found.')
        result = dict(row)
        result.pop('payload_json')
        result['result'] = json.loads(result.pop('result_json') or 'null')
        return result
    if method == 'GET' and path == '/api/jobs':
        return {'jobs': worker_jobs()}
    if method == 'GET' and path == '/api/status':
        settings = llm_settings.load()
        inference = False
        models = []
        try:
            with urlopen(Request(llm_settings.api_url(settings, 'models'), headers=llm_settings.headers(settings)), timeout=2) as response:
                models = [m['id'] for m in json.load(response).get('data', [])]
                inference = True
        except Exception:
            pass
        return {'inference': inference, 'engine': 'llama.cpp' if settings['format'] == 'local' else 'OpenAI-compatible', 'models': models, 'model': settings['model'],
                'model_ready': settings['model'] in models,
                'yt_dlp': importlib.util.find_spec('yt_dlp') is not None,
                'whisper': importlib.util.find_spec('faster_whisper') is not None,
                'whisper_model': config.WHISPER_MODEL, 'device': config.WHISPER_DEVICE}
    match = re.fullmatch(r'/api/videos/([A-Za-z0-9_-]{11})', path)
    if method == 'GET' and match:
        result = db.detail(match[1])
        if result is None:
            raise LookupError('Video not found.')
        return result
    if method == 'POST' and path == '/api/discover':
        url, is_feed = media.canonical_url(body.get('url', ''))
        limit = integer(body.get('limit', 50), 1, config.MAX_DISCOVERY)
        offset = integer(body.get('offset', 0), 0, 1000000)
        if offset and not is_feed:
            raise ValueError('Only channels and playlists support pagination.')
        return {'job_id': worker.enqueue('discover', {'url': url, 'limit': limit, 'offset': offset})}
    if method == 'POST' and path == '/api/library':
        ids = body.get('ids', [])
        if not isinstance(ids, list) or not 1 <= len(ids) <= 10000:
            raise ValueError('Select between 1 and 10000 videos.')
        ids = list(dict.fromkeys(valid_id(ident) for ident in ids))
        url, is_feed = media.canonical_url(body.get('url', ''))
        if not is_feed and (len(ids) != 1 or url != f'https://www.youtube.com/watch?v={ids[0]}'):
            raise ValueError('The source link must match the selected video.')
        return db.add_to_library(ids, url, is_feed)
    if method == 'POST' and path == '/api/process':
        ids = body.get('ids', [])
        if not isinstance(ids, list) or not 1 <= len(ids) <= 500:
            raise ValueError('Select between 1 and 500 videos.')
        ids = list(dict.fromkeys(valid_id(i) for i in ids))
        force = body.get('force', False)
        if not isinstance(force, bool):
            raise ValueError('force must be true or false.')
        return {'job_ids': [worker.enqueue('process', {'force': force}, ident) for ident in ids]}
    if method == 'POST' and path == '/api/retry':
        return {'job_id': worker.retry(str(body.get('id', '')))}
    raise LookupError('Endpoint not found.')


def worker_jobs():
    output = []
    for job in db.jobs():
        job.pop('payload_json')
        job['result'] = json.loads(job.pop('result_json') or 'null')
        output.append(job)
    return output


def application(environ, start_response):
    path = environ.get('PATH_INFO', '/')
    method = environ.get('REQUEST_METHOD', 'GET')
    headers = [('X-Content-Type-Options', 'nosniff'), ('Referrer-Policy', 'no-referrer'),
               ('Cache-Control', 'no-store'),
               ('Content-Security-Policy', "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")]
    status = '200 OK'
    content_type = 'application/json; charset=utf-8'
    try:
        if path == '/healthz':
            result = {'status': 'ok'}
        else:
            protected = path.startswith('/api/')
            if protected and TOKEN and not hmac.compare_digest(environ.get('HTTP_AUTHORIZATION', ''), 'Bearer ' + TOKEN):
                status, result = '401 Unauthorized', {'error': 'Enter the server access token in Settings.'}
            elif method == 'POST':
                # Require JSON and same-origin browser requests; this also blocks cross-site form submissions.
                origin = environ.get('HTTP_ORIGIN')
                if origin and origin.split('://', 1)[-1] != environ.get('HTTP_HOST'):
                    raise PermissionError('Cross-origin requests are not accepted.')
                if environ.get('CONTENT_TYPE', '').split(';')[0] != 'application/json':
                    raise ValueError('Requests must use application/json.')
                size = int(environ.get('CONTENT_LENGTH') or '0')
                if not 0 < size <= MAX_BODY:
                    raise ValueError('Request body is empty or too large.')
                body = json.loads(environ['wsgi.input'].read(size))
                if not isinstance(body, dict):
                    raise ValueError('Expected a JSON object.')
                result = api(method, path, {}, body)
            elif method == 'GET' and path.startswith('/api/export/'):
                video = db.detail(valid_id(path.removeprefix('/api/export/')))
                if not video:
                    raise LookupError('Video not found.')
                result = export_markdown(video).encode()
                content_type = 'text/markdown; charset=utf-8'
                headers.append(('Content-Disposition', f'attachment; filename="{video["id"]}.md"'))
            elif method == 'GET' and path.startswith('/api/'):
                query = {k: v[-1] for k, v in parse_qs(environ.get('QUERY_STRING', '')).items()}
                result = api(method, path, query, {})
            elif method == 'GET' and path in ('/', '/app.js', '/style.css'):
                name = 'index.html' if path == '/' else path.lstrip('/')
                result = (config.ROOT / 'static' / name).read_bytes()
                content_type = mimetypes.guess_type(name)[0] + '; charset=utf-8'
            else:
                raise LookupError('Not found.')
    except (ValueError, TypeError) as exc:
        status, result = '400 Bad Request', {'error': str(exc)}
    except LookupError as exc:
        status, result = '404 Not Found', {'error': str(exc)}
    except PermissionError as exc:
        status, result = '403 Forbidden', {'error': str(exc)}
    except Exception:
        logging.exception('Request failed')
        status, result = '500 Internal Server Error', {'error': 'Internal error. Check server logs.'}
    payload = result if isinstance(result, bytes) else json.dumps(result, ensure_ascii=False).encode()
    start_response(status, headers + [('Content-Type', content_type), ('Content-Length', str(len(payload)))])
    return [payload]


def start():
    db.init()
    worker.STOP.clear()
    for discovery, name in [(False, 'video-worker'), (True, 'discovery-worker')]:
        threading.Thread(target=worker.run, kwargs={'discovery': discovery}, name=name, daemon=True).start()
    return application


if __name__ == '__main__':
    from wsgiref.simple_server import make_server
    logging.basicConfig(level=logging.INFO)
    port = int(os.getenv('PORT', '8000'))
    print(f'Video research library: http://127.0.0.1:{port}', flush=True)
    # Bind first: a port collision must not start a second set of job workers.
    from socketserver import ThreadingMixIn
    from wsgiref.simple_server import WSGIServer

    class LocalServer(ThreadingMixIn, WSGIServer):
        daemon_threads = True

    with make_server('127.0.0.1', port, application, server_class=LocalServer) as server:
        start()
        try:
            server.serve_forever()
        finally:
            worker.STOP.set()
