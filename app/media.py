"""YouTube discovery and original-language transcription. Never request translation."""
import html
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen
from . import config

VIDEO_ID = re.compile(r'^[A-Za-z0-9_-]{11}$')


def canonical_url(value):
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError('Enter a valid YouTube video, playlist, or channel URL.')
    p = urlparse(value.strip())
    if p.scheme not in ('http', 'https') or p.username or p.password or p.port not in (None, 80, 443):
        raise ValueError('Only public YouTube links are supported.')
    if p.hostname not in ('youtube.com', 'www.youtube.com', 'm.youtube.com', 'youtu.be'):
        raise ValueError('Only YouTube links are supported.')
    q = parse_qs(p.query)
    path = p.path.rstrip('/')
    ident = None
    if p.hostname == 'youtu.be':
        ident = path.lstrip('/')
    elif path == '/watch':
        ident = q.get('v', [''])[0]
    elif re.match(r'^/(shorts|live|embed)/', path):
        ident = path.split('/')[2]
    if ident is not None:
        if not VIDEO_ID.fullmatch(ident):
            raise ValueError('Invalid YouTube video ID.')
        return f'https://www.youtube.com/watch?v={ident}', False
    if path == '/playlist' and re.fullmatch(r'[A-Za-z0-9_-]{10,100}', q.get('list', [''])[0]):
        return 'https://www.youtube.com/playlist?' + urlencode({'list': q['list'][0]}), True
    match = re.fullmatch(r'(/@[\w.\-\u0080-\uffff]+|/(?:channel|c|user)/[\w.-]+)(?:/(?:videos|posts|streams|shorts|featured))?', path)
    if match:
        return 'https://www.youtube.com' + match[1] + '/videos', True
    raise ValueError('Use a video, playlist, or channel URL (including /posts).')


def run_ytdlp(args, timeout=900):
    command = [sys.executable, '-m', 'yt_dlp', '--ignore-config', '--no-warnings',
               '--socket-timeout', '30', '--retries', '2', '--js-runtimes', 'node']
    cookies = os.getenv('YOUTUBE_COOKIES')
    if cookies:
        command += ['--cookies', cookies]
    process = subprocess.run(command + args, capture_output=True, text=True, timeout=timeout)
    if process.returncode:
        # Do not expose cookies or signed media URLs through the API.
        if 'No module named' in process.stderr:
            raise RuntimeError('yt-dlp is not installed. Install requirements.txt or use Docker Compose.')
        error = process.stderr.lower()
        if '429' in error or 'too many requests' in error:
            raise RuntimeError('YouTube rate-limited the request (HTTP 429). Wait before retrying.')
        if 'requested format is not available' in error or 'no video formats' in error:
            raise RuntimeError('YouTube returned no usable download formats. Update yt-dlp and check the JavaScript runtime.')
        if 'private video' in error or 'video unavailable' in error or 'has been removed' in error:
            raise RuntimeError('This video is private, removed, or unavailable to the server.')
        if 'sign in' in error or 'bot' in error:
            raise RuntimeError('YouTube requires authentication for this request. Configure YOUTUBE_COOKIES on the server.')
        if '403' in error:
            raise RuntimeError('YouTube denied this request (HTTP 403). Check yt-dlp updates and server access.')
        raise RuntimeError('YouTube could not provide this media. It may be private, unavailable, or rate limited. Try again later.')
    return process.stdout


def metadata(info):
    video_id = info.get('id', '')
    if not VIDEO_ID.fullmatch(video_id):
        raise ValueError('YouTube returned an invalid video ID.')
    stamp = info.get('release_timestamp') or info.get('timestamp')
    date = info.get('upload_date') or info.get('release_date')
    published = datetime.fromtimestamp(stamp, timezone.utc).isoformat() if stamp else (
        datetime.strptime(date, '%Y%m%d').replace(tzinfo=timezone.utc).isoformat() if date else None)
    return {'id': video_id, 'url': f'https://www.youtube.com/watch?v={video_id}',
            'title': info.get('title') or video_id, 'creator': info.get('channel') or info.get('uploader') or '',
            'channel_id': info.get('channel_id'), 'published_at': published,
            'duration': info.get('duration'), 'metadata': {
                'description': info.get('description', ''), 'tags': info.get('tags', []),
                'categories': info.get('categories', []), 'language': info.get('language'),
                'availability': info.get('availability'), 'date_precision': 'timestamp' if stamp else 'day',
            }}


def video_info(url, timeout=120):
    return json.loads(run_ytdlp(['--dump-single-json', '--skip-download', '--ignore-no-formats-error', '--no-playlist', url], timeout=timeout))


def discover(url, limit, progress=lambda _: None, offset=0):
    url, feed = canonical_url(url)
    if not feed:
        return {'videos': [metadata(video_info(url))], 'source': url, 'limit': 1, 'warnings': []}
    progress('Fetching the channel video list from YouTube')
    data = json.loads(run_ytdlp(['--dump-single-json', '--flat-playlist', '--playlist-start', str(offset + 1), '--playlist-end', str(offset + limit), url], timeout=120))
    entries = list(data.get('entries') or [])
    results, warnings = [], []
    for index, item in enumerate(entries):
        if not item or not VIDEO_ID.fullmatch(item.get('id', '')):
            continue
        listing = dict(item)
        progress(f'Reading publication dates {index + 1}/{len(entries)}')
        try:
            # Flat listings often omit dates. Resolve each entry, never silently invent them.
            if not (item.get('timestamp') or item.get('upload_date') or item.get('release_timestamp')):
                item = video_info(f"https://www.youtube.com/watch?v={item['id']}", timeout=60)
            results.append(metadata(item))
        except (ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
            reason = str(exc) if isinstance(exc, RuntimeError) else ('Metadata request timed out.' if isinstance(exc, subprocess.TimeoutExpired) else 'Invalid metadata returned by YouTube.')
            warnings.append(f"{listing['id']}: {reason}")
            # Retain the flat listing instead of silently losing selectable videos.
            fallback = metadata({k: v for k, v in listing.items() if k not in ('timestamp', 'release_timestamp', 'upload_date', 'release_date')})
            fallback['metadata_error'] = reason
            results.append(fallback)
    results.sort(key=lambda v: v.get('published_at') or '', reverse=True)
    return {'videos': results, 'source': url, 'limit': limit, 'warnings': warnings,
            'possibly_more': len(entries) >= limit, 'scanned': len(entries),
            'offset': offset, 'next_offset': offset + len(entries) if len(entries) >= limit else None}


def original_language(info):
    original_audio = {f['language'] for f in info.get('formats', [])
                      if f.get('language') and f.get('acodec') not in (None, 'none')
                      and ((f.get('language_preference') or 0) >= 10
                           or '(original)' in (f.get('format_note') or '').lower())}
    if len(original_audio) == 1:
        return next(iter(original_audio))
    if len(original_audio) > 1:
        return None
    if info.get('language'):
        return info['language']
    # Multiple dubbed tracks may each have an -orig caption key. Never pick by order.
    languages = {k.removesuffix('-orig') for k in (info.get('automatic_captions') or {}) if k.endswith('-orig')}
    return next(iter(languages)) if len(languages) == 1 else None


def audio_format(info):
    """Select original audio without mistaking yt-dlp's unknown preference (-1) for a dub."""
    formats = [f for f in info.get('formats', []) if f.get('acodec') not in (None, 'none')]
    if not formats:
        raise RuntimeError('YouTube returned no usable audio formats. Retry after updating yt-dlp.')

    def usable(f):
        note = (f.get('format_note') or '').lower()
        return ((f.get('language_preference') or 0) >= -1
                and not any(label in note for label in ('dubbed', 'auto-dub', 'descriptive'))
                and not (f.get('language') or '').endswith('-desc'))

    candidates = [f for f in formats if usable(f)]
    original = [f for f in candidates if (f.get('language_preference') or 0) >= 10
                or '(original)' in (f.get('format_note') or '').lower()]
    language = original_language(info)
    if not original and language:
        original = [f for f in candidates if (f.get('language') or '').split('-')[0] == language.split('-')[0]]
    if not original:
        languages = {f['language'].split('-')[0] for f in formats if f.get('language')}
        # Unlabelled single-language audio is common. Never fall back to a known
        # different language or an explicitly dubbed/descriptive track.
        if (len(candidates) == len(formats) and len(languages) <= 1
                and (not language or not languages or languages == {language.split('-')[0]})):
            original = candidates
    if original:
        best = max(original, key=lambda f: (f.get('vcodec') == 'none', f.get('abr') or f.get('tbr') or 0))
        return best['format_id']
    languages = ', '.join(sorted({f.get('language') or 'unknown' for f in formats}))
    raise RuntimeError('Original audio could not be identified safely. '
                       f'Expected language: {language or "unknown"}; available audio languages: {languages}. '
                       'Retry after updating yt-dlp. Translated or ambiguous audio was not selected.')


def native_track(info):
    """Select only provably untranslated captions, never an English fallback."""
    automatic = info.get('automatic_captions') or {}
    language = original_language(info)
    if not language:
        return None
    for tracks, keys in [(info.get('subtitles') or {}, [language]),
                         (automatic, [language + '-orig', language])]:
        variants = [k for k in tracks if k.removesuffix('-orig').split('-')[0] == language.split('-')[0]]
        for key in dict.fromkeys(keys + variants):
            for track in tracks.get(key, []):
                url = track.get('url', '')
                p = urlparse(url)
                q = parse_qs(p.query, keep_blank_values=True)
                if track.get('ext') != 'json3' or 'tlang' in q:
                    continue
                if p.scheme != 'https' or p.hostname not in ('www.youtube.com', 'youtube.com'):
                    continue
                if q.get('lang', [language])[0].split('-')[0] != language.split('-')[0]:
                    continue
                return language, url, 'youtube_original_captions'
    return None


def parse_json3(data):
    segments = []
    for event in data.get('events', []):
        text = html.unescape(''.join(s.get('utf8', '') for s in event.get('segs', []))).strip()
        if not text:
            continue
        start = float(event.get('tStartMs', 0)) / 1000
        segment = {'start': start, 'end': start + float(event.get('dDurationMs', 0)) / 1000, 'text': text}
        if not segments or segments[-1]['text'] != text:
            segments.append(segment)
    return segments


_whisper = None


def decode_audio_ffmpeg(path):
    """Supply Whisper with 16 kHz mono float32 samples, bypassing PyAV file decoding."""
    import numpy as np
    try:
        decoded = subprocess.run([
            'ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error',
            '-i', str(path), '-map', '0:a:0', '-vn', '-ac', '1', '-ar', '16000',
            '-f', 'f32le', '-acodec', 'pcm_f32le', 'pipe:1',
        ], capture_output=True, timeout=900)
    except FileNotFoundError as exc:
        raise RuntimeError('ffmpeg is not installed. Install ffmpeg on the host and retry.') from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError('Audio decoding timed out after 15 minutes. Retry this video.') from exc
    if decoded.returncode:
        raise RuntimeError('ffmpeg could not decode the downloaded audio. Retry to download a fresh copy.')
    if not decoded.stdout or len(decoded.stdout) % 4:
        raise RuntimeError('Audio decoding returned empty or invalid samples.')
    return np.frombuffer(decoded.stdout, dtype='<f4').astype(np.float32, copy=True)


def transcribe(info, progress=lambda _: None):
    track = native_track(info)
    if track:
        progress('Fetching original-language captions')
        try:
            with urlopen(Request(track[1], headers={'User-Agent': 'Mozilla/5.0'}), timeout=40) as response:
                segments = parse_json3(json.load(response))
            if segments:
                return track[0], track[2], segments
        except Exception:
            progress('Original captions unavailable; transcribing audio')
    global _whisper
    if config.WHISPER_MODEL.endswith('.en'):
        raise RuntimeError('Use a multilingual Whisper model, not an English-only .en model.')
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError('Original captions are unavailable. Install faster-whisper to transcribe audio.') from exc
    with tempfile.TemporaryDirectory(prefix='audio-', dir=config.DATA) as tmp:
        progress('Downloading audio for native-language detection and transcription')
        run_ytdlp(['--no-playlist', '-f', audio_format(info), '--max-filesize', '1G',
                   '-o', str(Path(tmp) / 'audio.%(ext)s'), f"https://www.youtube.com/watch?v={info['id']}"])
        paths = [p for p in Path(tmp).glob('audio.*') if p.suffix not in ('.part', '.ytdl')]
        if not paths:
            raise RuntimeError('Audio download failed or exceeded the 1 GB limit.')
        progress('Decoding audio with ffmpeg')
        audio = decode_audio_ffmpeg(paths[0])
        if _whisper is None:
            progress(f'Loading Whisper model {config.WHISPER_MODEL} (first run may download model files)')
            try:
                _whisper = WhisperModel(config.WHISPER_MODEL, device=config.WHISPER_DEVICE,
                                        compute_type='int8' if config.WHISPER_DEVICE == 'cpu' else 'float16')
            except (RuntimeError, ValueError):
                _whisper = WhisperModel(config.WHISPER_MODEL, device='cpu', compute_type='int8')
        progress('Transcribing in the detected language (may take several minutes on CPU)')
        try:
            segments, detected = _whisper.transcribe(audio, task='transcribe', language=None, vad_filter=True)
            output = [{'start': s.start, 'end': s.end, 'text': s.text.strip()} for s in segments if s.text.strip()]
        except RuntimeError:
            if config.WHISPER_DEVICE == 'cpu':
                raise
            _whisper = WhisperModel(config.WHISPER_MODEL, device='cpu', compute_type='int8')
            segments, detected = _whisper.transcribe(audio, task='transcribe', language=None, vad_filter=True)
            output = [{'start': s.start, 'end': s.end, 'text': s.text.strip()} for s in segments if s.text.strip()]
        if not output:
            raise RuntimeError('No speech was detected in this video.')
        return detected.language, 'faster_whisper', output
