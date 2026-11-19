"""Persistent inference configuration; credentials never appear in API responses."""
import json
import os
import tempfile
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from urllib.parse import urlsplit, urlunsplit
from . import config

_lock = threading.RLock()
_active = ContextVar('llm_settings', default=None)


def load():
    defaults = {'endpoint': config.LLM_URL, 'model': config.LLM_MODEL,
                'api_key': os.getenv('LLM_API_KEY', ''), 'format': 'local'}
    with _lock:
        path = config.DATA / 'llm-settings.json'
        if path.exists():
            defaults.update(json.loads(path.read_text()))
    return defaults


def current():
    return _active.get() or load()


@contextmanager
def use(settings):
    token = _active.set(settings)
    try:
        yield
    finally:
        _active.reset(token)


def public(settings=None):
    value = dict(settings if settings is not None else load())
    value['key_configured'] = bool(value.pop('api_key', ''))
    return value


def validate(body, previous):
    endpoint = body.get('endpoint', previous['endpoint'])
    model = body.get('model', previous['model'])
    mode = body.get('format', previous['format'])
    if not isinstance(endpoint, str) or len(endpoint) > 2048:
        raise ValueError('Enter an HTTP(S) API base URL.')
    parsed = urlsplit(endpoint.strip())
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or any(c.isspace() for c in endpoint)):
        raise ValueError('Use an HTTP(S) base URL without credentials, query parameters, or fragments.')
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError('Invalid API endpoint port.') from exc
    if not isinstance(model, str) or not model.strip() or len(model) > 200:
        raise ValueError('Enter a model name or alias.')
    if mode not in ('local', 'json', 'text'):
        raise ValueError('Choose a supported response format.')
    key = body.get('api_key', '')
    clear = body.get('clear_key', False)
    if not isinstance(clear, bool) or not isinstance(key, str) or len(key) > 8192 or '\n' in key or '\r' in key:
        raise ValueError('Invalid API key.')
    return {'endpoint': urlunsplit(parsed).rstrip('/'), 'model': model.strip(), 'format': mode,
            'api_key': '' if clear else key.strip() or previous['api_key']}


def save(body):
    with _lock:
        value = validate(body, load())
        config.DATA.mkdir(parents=True, exist_ok=True)
        fd, path = tempfile.mkstemp(prefix='.llm-', dir=config.DATA)
        try:
            with os.fdopen(fd, 'w') as output:
                json.dump(value, output)
            os.replace(path, config.DATA / 'llm-settings.json')
        finally:
            if os.path.exists(path):
                os.unlink(path)
    return public(value)


def api_url(settings, route):
    base = settings['endpoint'].rstrip('/')
    if not urlsplit(base).path:
        base += '/v1'
    return base + '/' + route


def headers(settings):
    result = {'Content-Type': 'application/json'}
    if settings['api_key']:
        result['Authorization'] = 'Bearer ' + settings['api_key']
    return result
