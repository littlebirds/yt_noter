"""Read-only audio-selection diagnostics: python -m app.diagnose VIDEO_URL."""
import argparse
import inspect
import json
import sqlite3
from importlib.metadata import PackageNotFoundError, version

from . import config, media


def report(url):
    url, feed = media.canonical_url(url)
    if feed:
        raise ValueError('Provide a single video URL.')
    ident = url.rsplit('=', 1)[-1]
    try:
        installed = version('yt-dlp')
    except PackageNotFoundError:
        installed = 'not installed'
    result = {'video_url': url, 'yt_dlp_version': installed,
              'audio_selection_fix_loaded': 'def usable(f)' in inspect.getsource(media.audio_format)}
    if config.DB.exists():
        con = sqlite3.connect(f'{config.DB.as_uri()}?mode=ro', uri=True)
        try:
            row = con.execute("SELECT status,progress,error,updated_at FROM jobs WHERE video_id=? AND kind='process' ORDER BY created_at DESC LIMIT 1", (ident,)).fetchone()
            if row:
                result['latest_job'] = dict(zip(('status', 'progress', 'error', 'updated_at'), row))
        finally:
            con.close()
    # Print local evidence first, even if YouTube metadata lookup times out.
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    info = media.video_info(url)
    fields = ('format_id', 'acodec', 'vcodec', 'language', 'language_preference', 'format_note', 'abr')
    tracks = [{key: f.get(key) for key in fields} for f in info.get('formats', [])
              if f.get('acodec') not in (None, 'none')]
    result = {'metadata_language': info.get('language'),
              'detected_original_language': media.original_language(info),
              'original_caption_keys': [key for key in (info.get('automatic_captions') or {}) if key.endswith('-orig')],
              'audio_formats': tracks}
    try:
        result['selected_audio_format'] = media.audio_format(info)
    except RuntimeError as exc:
        result['selection_error'] = str(exc)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('url')
    args = parser.parse_args()
    try:
        report(args.url)
    except Exception as exc:
        # Expected media errors are sanitized by media.video_info; other exceptions
        # may contain signed URLs, so output only their type.
        message = str(exc) if isinstance(exc, (ValueError, RuntimeError)) else type(exc).__name__
        print(json.dumps({'diagnostic_error': message}, ensure_ascii=False))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
