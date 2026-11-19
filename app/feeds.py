"""Persistent feed checkpoints and paged backfill; no fixed history cap."""
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from . import db, media, channel_feed

POLL_MINUTES = max(5, int(os.getenv('FEED_POLL_MINUTES', '15')))
PAGE_SIZE = 25
RECONCILE_HOURS = max(1, int(os.getenv('FEED_RECONCILE_HOURS', '6')))


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError('Go-back time must be an ISO timestamp with a timezone.')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise ValueError('Invalid go-back time.') from exc
    if parsed.tzinfo is None:
        raise ValueError('Go-back time requires a timezone.')
    if parsed > datetime.now(timezone.utc) + timedelta(minutes=1):
        raise ValueError('Go-back time cannot be in the future.')
    return parsed.astimezone(timezone.utc).isoformat()


def subscribe(url, since):
    since = timestamp(since)
    with db.connect() as con:
        con.execute('BEGIN IMMEDIATE')
        row = con.execute('SELECT * FROM sources WHERE url=?', (url,)).fetchone()
        if row:
            # Re-pasting a feed never moves its lower bound forward and skips history.
            if since < row['since']:
                active = con.execute("SELECT 1 FROM jobs WHERE kind='sync' AND status IN ('queued','running') AND json_extract(payload_json,'$.source_id')=?", (row['id'],)).fetchone()
                if active:
                    raise ValueError('This feed is currently scanning. Wait for it to finish before extending its history.')
                con.execute('UPDATE sources SET since=?,checkpoint=NULL,cursor=0,scan_started=NULL,next_scan=? WHERE id=?', (since, db.now(), row['id']))
            con.execute('UPDATE sources SET enabled=1 WHERE id=?', (row['id'],))
            return row['id']
        ident = uuid.uuid4().hex
        con.execute('INSERT INTO sources(id,url,since,next_scan,created_at) VALUES(?,?,?,?,?)', (ident, url, since, db.now(), db.now()))
        return ident


def sources():
    with db.connect() as con:
        return [dict(r) for r in con.execute('''SELECT s.*, (SELECT count(*) FROM source_videos v WHERE v.source_id=s.id) AS discovered
                                                FROM sources s ORDER BY created_at DESC''')]


def update_source(ident, enabled):
    if not isinstance(enabled, bool):
        raise ValueError('enabled must be true or false.')
    with db.connect() as con:
        result = con.execute('UPDATE sources SET enabled=?,next_scan=? WHERE id=?', (int(enabled), db.now(), ident))
        if not result.rowcount:
            raise LookupError('Feed not found.')
        if not enabled:
            con.execute("UPDATE jobs SET status='cancelled',progress='Monitoring stopped',updated_at=? WHERE kind='sync' AND status='queued' AND json_extract(payload_json,'$.source_id')=?", (db.now(), ident))


def schedule():
    # Queue insertion and duplicate checks are atomic, including restarts.
    with db.connect() as con:
        con.execute('BEGIN IMMEDIATE')
        for source in con.execute('SELECT * FROM sources WHERE enabled=1 AND next_scan<=?', (db.now(),)).fetchall():
            active = con.execute("SELECT 1 FROM jobs WHERE kind='sync' AND status IN ('queued','running') AND json_extract(payload_json,'$.source_id')=?", (source['id'],)).fetchone()
            if active:
                continue
            ident = uuid.uuid4().hex
            con.execute('''INSERT INTO jobs(id,kind,payload_json,status,created_at,updated_at)
                           VALUES(?,?,?,?,?,?)''', (ident, 'sync', db.dumps({'source_id': source['id']}), 'queued', db.now(), db.now()))


def sync(source_id, progress):
    from .worker import enqueue
    with db.connect() as con:
        row = con.execute('SELECT * FROM sources WHERE id=?', (source_id,)).fetchone()
        if not row or not row['enabled']:
            return {'paused': True}
        source = dict(row)
        started = source['scan_started'] or db.now()
        if not source['channel_id'] and '/playlist?' not in source['url']:
            direct = source['url'].split('/channel/')[-1].split('/')[0] if '/channel/' in source['url'] else ''
            cached = con.execute('SELECT DISTINCT v.channel_id FROM videos v JOIN library_links l ON l.video_id=v.id WHERE l.source_url=? AND v.channel_id IS NOT NULL', (source['url'],)).fetchall()
            candidates = {r[0] for r in cached}
            candidate = direct or (next(iter(candidates)) if len(candidates) == 1 else '')
            if channel_feed.CHANNEL_ID.fullmatch(candidate):
                source['channel_id'] = candidate
                con.execute('UPDATE sources SET channel_id=? WHERE id=?', (candidate, source_id))
    since = datetime.fromisoformat(source['since'])
    # Revisit one day to catch delayed listings; source_videos prevents duplicate jobs.
    cutoff = max(since, datetime.fromisoformat(source['checkpoint']) - timedelta(days=1)) if source['checkpoint'] else since
    try:
        current = datetime.now(timezone.utc)
        recent_scan = source['last_reconciled'] and current - datetime.fromisoformat(source['last_reconciled']) < timedelta(hours=RECONCILE_HOURS)
        recent_check = source['last_checked'] and current - datetime.fromisoformat(source['last_checked']) < timedelta(minutes=max(60, POLL_MINUTES * 3))
        if source['channel_id'] and recent_scan and recent_check and not source['scan_started'] and not source['cursor']:
            try:
                progress('Checking channel feed for new uploads')
                videos = channel_feed.read(source['channel_id'])
                queued = 0
                for video in videos:
                    if datetime.fromisoformat(video['published_at']) < since or datetime.fromisoformat(video['published_at']) > current:
                        continue
                    with db.connect() as con:
                        enabled = con.execute('SELECT enabled FROM sources WHERE id=?', (source_id,)).fetchone()[0]
                        known = con.execute('SELECT 1 FROM source_videos WHERE source_id=? AND video_id=?', (source_id, video['id'])).fetchone()
                    if not enabled:
                        return {'paused': True}
                    if not known:
                        if not db.detail(video['id']):
                            db.save_video(video)
                        saved = db.add_to_library([video['id']], source['url'], True)
                        queued += len(saved['job_ids'])
                with db.connect() as con:
                    con.execute('UPDATE sources SET last_checked=?,last_error=NULL,next_scan=? WHERE id=?', (db.now(), (current + timedelta(minutes=POLL_MINUTES)).isoformat(), source_id))
                return {'source_id': source_id, 'queued': queued, 'method': 'feed'}
            except Exception:
                progress('Channel feed unavailable; checking the video listing instead')
        with db.connect() as con:
            con.execute('UPDATE sources SET scan_started=? WHERE id=?', (started, source_id))
        result = media.discover(source['url'], PAGE_SIZE, progress, offset=source['cursor'])
        if not source['channel_id'] and '/playlist?' not in source['url']:
            candidates = {v.get('channel_id') for v in result['videos'] if v.get('channel_id')}
            if len(candidates) == 1:
                candidate = next(iter(candidates))
                if channel_feed.CHANNEL_ID.fullmatch(candidate):
                    with db.connect() as con:
                        con.execute('UPDATE sources SET channel_id=? WHERE id=?', (candidate, source_id))
        incomplete = bool(result['warnings'])
        queued = 0
        for video in result['videos']:
            # Unknown dates cannot safely be filtered: retain the scan and retry later.
            if not video['published_at'] or video.get('metadata_error'):
                incomplete = True
                continue
            published = datetime.fromisoformat(video['published_at'])
            # A day-only date represents an interval, not a precise midnight upload.
            end = published + timedelta(days=1) if video.get('metadata', {}).get('date_precision') == 'day' else published
            if end < cutoff or published > datetime.now(timezone.utc):
                continue
            with db.connect() as con:
                known = con.execute('SELECT 1 FROM source_videos WHERE source_id=? AND video_id=?', (source_id, video['id'])).fetchone()
                complete = con.execute('SELECT 1 FROM summaries WHERE video_id=?', (video['id'],)).fetchone()
            with db.connect() as con:
                active = con.execute('SELECT enabled FROM sources WHERE id=?', (source_id,)).fetchone()
            if not active or not active['enabled']:
                return {'paused': True, 'queued': queued}
            if not known:
                db.save_video(video)
                db.mark_library(video['id'], source['url'])
                # Enqueue BEFORE marking seen. Crash recovery can only duplicate an idempotent job.
                if not complete:
                    enqueue('process', {}, video['id'])
                    queued += 1
                with db.connect() as con:
                    con.execute('INSERT OR IGNORE INTO source_videos VALUES(?,?,?)', (source_id, video['id'], db.now()))
        if incomplete:
            raise RuntimeError('Readable videos were queued, but some metadata is unavailable. This page will retry without advancing its checkpoint. Check discovery warnings for details.')
        oldest = min((datetime.fromisoformat(v['published_at']) + (timedelta(days=1) if v.get('metadata', {}).get('date_precision') == 'day' else timedelta()) for v in result['videos']), default=None)
        is_channel = '/playlist?' not in source['url']
        # Channel uploads are newest-first. Playlists can be arbitrary: scan all pages.
        exhausted = not result.get('possibly_more') or (is_channel and oldest is not None and oldest < cutoff)
        with db.connect() as con:
            if exhausted:
                con.execute('''UPDATE sources SET last_reconciled=?,checkpoint=?,cursor=0,scan_started=NULL,last_checked=?,last_error=NULL,next_scan=? WHERE id=?''',
                            (db.now(), started, db.now(), (datetime.now(timezone.utc) + timedelta(minutes=POLL_MINUTES)).isoformat(), source_id))
            else:
                # Overlap pages to tolerate uploads arriving while a long backfill runs.
                advance = max(1, result.get('scanned', PAGE_SIZE) - 5)
                con.execute('UPDATE sources SET cursor=?,last_checked=?,last_error=NULL,next_scan=? WHERE id=?',
                            (source['cursor'] + advance, db.now(), db.now(), source_id))
        return {'source_id': source_id, 'queued': queued, 'backfill_complete': exhausted}
    except Exception:
        with db.connect() as con:
            con.execute('UPDATE sources SET last_error=?,next_scan=? WHERE id=?',
                        ('Scan failed; the same page will be retried automatically.',
                         (datetime.now(timezone.utc) + timedelta(minutes=POLL_MINUTES)).isoformat(), source_id))
        raise
