"""SQLite persistence; each operation owns a short-lived connection."""
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from . import config, search


def now():
    return datetime.now(timezone.utc).isoformat()


def dumps(value):
    return json.dumps(value, ensure_ascii=False)


@contextmanager
def connect():
    config.DATA.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(config.DB, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    try:
        with con:
            yield con
    finally:
        con.close()



def init():
    with connect() as con:
        con.execute('PRAGMA journal_mode=WAL')
        con.executescript('''
        CREATE TABLE IF NOT EXISTS schema_version(version INTEGER PRIMARY KEY);
        INSERT OR IGNORE INTO schema_version VALUES(1);
        INSERT OR IGNORE INTO schema_version VALUES(2);
        CREATE TABLE IF NOT EXISTS videos(
            id TEXT PRIMARY KEY, url TEXT NOT NULL, title TEXT NOT NULL,
            creator TEXT NOT NULL DEFAULT '', channel_id TEXT,
            published_at TEXT, duration REAL, language TEXT,
            category TEXT NOT NULL DEFAULT 'other', metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS videos_date ON videos(published_at);
        CREATE INDEX IF NOT EXISTS videos_creator ON videos(creator);
        CREATE INDEX IF NOT EXISTS videos_category ON videos(category);
        CREATE TABLE IF NOT EXISTS transcripts(
            video_id TEXT PRIMARY KEY REFERENCES videos(id), language TEXT NOT NULL,
            source TEXT NOT NULL, text TEXT NOT NULL, segments_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS summaries(
            video_id TEXT PRIMARY KEY REFERENCES videos(id), markdown TEXT NOT NULL,
            model TEXT NOT NULL, data_json TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS mentions(
            id INTEGER PRIMARY KEY, video_id TEXT NOT NULL REFERENCES videos(id),
            ticker TEXT NOT NULL, stance TEXT NOT NULL, evidence TEXT NOT NULL,
            timestamp REAL NOT NULL, extra_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS mentions_ticker ON mentions(ticker, video_id);
        CREATE TABLE IF NOT EXISTS jobs(
            id TEXT PRIMARY KEY, kind TEXT NOT NULL, payload_json TEXT NOT NULL,
            video_id TEXT, status TEXT NOT NULL, progress TEXT NOT NULL DEFAULT '',
            result_json TEXT, error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status, created_at);
        CREATE TABLE IF NOT EXISTS sources(
            id TEXT PRIMARY KEY, url TEXT UNIQUE NOT NULL, since TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1, checkpoint TEXT, cursor INTEGER NOT NULL DEFAULT 0,
            scan_started TEXT, next_scan TEXT NOT NULL, last_checked TEXT, last_error TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS library_items(
            video_id TEXT PRIMARY KEY REFERENCES videos(id), saved_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS library_links(
            source_url TEXT NOT NULL, video_id TEXT NOT NULL REFERENCES videos(id),
            PRIMARY KEY(source_url, video_id)
        );
        CREATE TABLE IF NOT EXISTS source_videos(
            source_id TEXT NOT NULL REFERENCES sources(id), video_id TEXT NOT NULL,
            first_seen TEXT NOT NULL, PRIMARY KEY(source_id, video_id)
        );
        ''')
        if not con.execute('SELECT 1 FROM schema_version WHERE version=4').fetchone():
            con.execute('ALTER TABLE sources ADD COLUMN channel_id TEXT')
            con.execute('ALTER TABLE sources ADD COLUMN last_reconciled TEXT')
            con.execute('INSERT INTO schema_version VALUES(4)')
        # Preserve the pre-existing library once; later processing results are drafts.
        if not con.execute('SELECT 1 FROM schema_version WHERE version=3').fetchone():
            con.execute('INSERT OR IGNORE INTO library_items SELECT id,created_at FROM videos')
            con.execute('INSERT OR IGNORE INTO library_links SELECT url,id FROM videos')
            con.execute('INSERT INTO schema_version VALUES(3)')
        search.initialize(con)
        # Retire removed capture jobs without deleting historical records or media.
        con.execute("UPDATE jobs SET status='cancelled', progress='Capture feature removed', error=NULL, updated_at=? WHERE kind='frame' AND status IN ('queued','running','failed')", (now(),))
        # Single worker process: interrupted operations can safely be retried.
        con.execute("UPDATE jobs SET status='queued', progress='Resuming after restart', updated_at=? WHERE status='running'", (now(),))


def save_video(video):
    with connect() as con:
        con.execute('''INSERT INTO videos(id,url,title,creator,channel_id,published_at,duration,metadata_json,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
        title=excluded.title, creator=excluded.creator, channel_id=excluded.channel_id,
        published_at=COALESCE(excluded.published_at,videos.published_at),
        duration=COALESCE(excluded.duration,videos.duration), metadata_json=excluded.metadata_json,
        updated_at=excluded.updated_at''', (
            video['id'], video['url'], video['title'], video.get('creator', ''),
            video.get('channel_id'), video.get('published_at'), video.get('duration'),
            dumps(video.get('metadata', {})), now(), now()))
        search.sync(con, video['id'])


def save_transcript(video_id, language, source, segments):
    text = '\n'.join(s['text'] for s in segments)
    with connect() as con:
        con.execute('INSERT OR REPLACE INTO transcripts VALUES(?,?,?,?,?,?)',
                    (video_id, language, source, text, dumps(segments), now()))
        con.execute('UPDATE videos SET language=?, updated_at=? WHERE id=?', (language, now(), video_id))
        search.sync(con, video_id)


def save_summary(video_id, result, model):
    with connect() as con:
        con.execute('INSERT OR REPLACE INTO summaries VALUES(?,?,?,?,?)',
                    (video_id, result['summary'], model, dumps(result), now()))
        con.execute('UPDATE videos SET category=?,updated_at=? WHERE id=?',
                    (result['category'], now(), video_id))
        con.execute('DELETE FROM mentions WHERE video_id=?', (video_id,))
        for mention in result['mentions']:
            con.execute('''INSERT INTO mentions(video_id,ticker,stance,evidence,timestamp,extra_json)
                           VALUES(?,?,?,?,?,?)''',
                        (video_id, mention['ticker'], mention['stance'], mention['evidence'],
                         mention['timestamp'], dumps(mention.get('extra', {}))))

        search.sync(con, video_id)


def detail(video_id):
    with connect() as con:
        row = con.execute('SELECT * FROM videos WHERE id=?', (video_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        for table, key in [('transcripts', 'transcript'), ('summaries', 'summary')]:
            item = con.execute(f'SELECT * FROM {table} WHERE video_id=?', (video_id,)).fetchone()
            result[key] = dict(item) if item else None
        if result['transcript']:
            result['transcript']['segments'] = json.loads(result['transcript'].pop('segments_json'))
        result['saved'] = bool(con.execute('SELECT 1 FROM library_items WHERE video_id=?', (video_id,)).fetchone())
        result['source_urls'] = [r[0] for r in con.execute('SELECT source_url FROM library_links WHERE video_id=?', (video_id,))]
        result['mentions'] = [dict(r) for r in con.execute('SELECT * FROM mentions WHERE video_id=?', (video_id,))]
        return result


def library(filters):
    with connect() as con:
        ids = search.find(con, filters, limit=1000)
        rows_by_id = {r['id']: r for r in con.execute("""SELECT v.*, s.markdown AS summary_preview,
            t.source AS transcript_source FROM videos v
            LEFT JOIN transcripts t ON t.video_id=v.id
            LEFT JOIN summaries s ON s.video_id=v.id
            WHERE v.id IN (SELECT value FROM json_each(?))
              AND EXISTS(SELECT 1 FROM library_items l WHERE l.video_id=v.id)""", (dumps(ids),))}
        rows = [rows_by_id[ident] for ident in ids if ident in rows_by_id]
        creators = [r[0] for r in con.execute("SELECT DISTINCT creator FROM videos WHERE creator<>'' AND id IN (SELECT video_id FROM library_items) ORDER BY creator")]
        stats = dict(con.execute('''SELECT (SELECT count(*) FROM library_items) AS videos,
            (SELECT count(*) FROM transcripts WHERE video_id IN (SELECT video_id FROM library_items)) AS transcripts,
            (SELECT count(*) FROM summaries WHERE video_id IN (SELECT video_id FROM library_items)) AS summaries,
            (SELECT count(DISTINCT ticker) FROM mentions WHERE video_id IN (SELECT video_id FROM library_items)) AS tickers''').fetchone())
        videos = [dict(r) for r in rows]
        groups = [dict(r) for r in con.execute('SELECT id,url,enabled FROM sources ORDER BY created_at DESC')]
        for group in groups:
            linked = {r[0] for r in con.execute('SELECT video_id FROM library_links WHERE source_url=?', (group['url'],))}
            group['video_ids'] = [v['id'] for v in videos if v['id'] in linked]
            names = {v['creator'] for v in videos if v['id'] in linked and v['creator']}
            group['title'] = next(iter(names)) if len(names) == 1 else group['url']
    return {'videos': videos, 'groups': groups, 'creators': creators, 'stats': stats, 'limit': 1000}


def jobs():
    with connect() as con:
        rows = con.execute("""WITH ranked AS (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY CASE WHEN kind='process' AND video_id IS NOT NULL THEN 'video:' || video_id ELSE 'job:' || id END
                ORDER BY CASE status WHEN 'running' THEN 0 WHEN 'queued' THEN 1 ELSE 2 END,
                         updated_at DESC,created_at DESC,id DESC) AS rank
            FROM jobs
        ), visible AS (SELECT * FROM ranked WHERE rank=1)
        SELECT j.*,v.title,v.creator FROM visible j LEFT JOIN videos v ON v.id=j.video_id
        WHERE j.status IN ('queued','running') OR j.id IN
            (SELECT id FROM visible WHERE status NOT IN ('queued','running') ORDER BY updated_at DESC LIMIT 100)
        ORDER BY CASE j.status WHEN 'running' THEN 0 WHEN 'queued' THEN 1 ELSE 2 END,
                 CASE WHEN j.status IN ('running','queued') THEN j.created_at END ASC,j.updated_at DESC,j.id""").fetchall()
        titles = {}
        for row in con.execute("SELECT result_json FROM jobs WHERE kind='discover' AND status='done' ORDER BY created_at DESC LIMIT 100"):
            for video in json.loads(row[0] or '{}').get('videos', []):
                titles.setdefault(video['id'], video)
        result = []
        positions = {'discovery': 0, 'processing': 0}
        for row in rows:
            job = dict(row)
            job.pop('rank', None)
            info = titles.get(job['video_id'], {})
            job['title'] = job['title'] or info.get('title')
            job['creator'] = job['creator'] or info.get('creator')
            job['lane'] = 'discovery' if job['kind'] == 'discover' else 'processing'
            job['queue_position'] = None
            if job['status'] == 'queued':
                positions[job['lane']] += 1
                job['queue_position'] = positions[job['lane']]
            if job['kind'] in ('sync', 'discover'):
                payload = json.loads(job['payload_json'])
                source = con.execute('SELECT url FROM sources WHERE id=?', (payload.get('source_id'),)).fetchone()
                job['source_url'] = source[0] if source else payload.get('url', '')
            result.append(job)
        return result


def summary_ids(ids):
    if not ids:
        return set()
    with connect() as con:
        return {r[0] for r in con.execute('SELECT video_id FROM summaries WHERE video_id IN (' + ','.join('?' for _ in ids) + ')', ids)}


def mark_library(video_id, source_url):
    """Save automatically discovered uploads from an already-saved subscription."""
    with connect() as con:
        con.execute('INSERT OR IGNORE INTO library_items VALUES(?,?)', (video_id, now()))
        con.execute('INSERT OR IGNORE INTO library_links VALUES(?,?)', (source_url, video_id))
        search.sync(con, video_id)


def add_to_library(ids, source_url, is_feed):
    with connect() as con:
        con.execute('BEGIN IMMEDIATE')
        # Discovery previews are enough to save links; transcription is independent.
        previews = {}
        for row in con.execute("SELECT result_json FROM jobs WHERE kind='discover' AND status='done' ORDER BY created_at DESC LIMIT 100"):
            for video in json.loads(row[0] or '{}').get('videos', []):
                previews.setdefault(video['id'], video)
        added = linked = 0
        job_ids = []
        for ident in ids:
            url = f'https://www.youtube.com/watch?v={ident}'
            preview = previews.get(ident, {})
            con.execute('''INSERT OR IGNORE INTO videos(id,url,title,creator,channel_id,published_at,duration,metadata_json,created_at,updated_at)
                           VALUES(?,?,?,?,?,?,?,?,?,?)''', (ident, url, preview.get('title') or url,
                           preview.get('creator') or '', preview.get('channel_id'), preview.get('published_at'),
                           preview.get('duration'), dumps(preview.get('metadata', {})), now(), now()))
            added += con.execute('INSERT OR IGNORE INTO library_items VALUES(?,?)', (ident, now())).rowcount
            linked += con.execute('INSERT OR IGNORE INTO library_links VALUES(?,?)', (source_url, ident)).rowcount
            search.sync(con, ident)
            # Save and enqueue in one transaction so a crash cannot leave an unqueued link.
            if not con.execute('SELECT 1 FROM summaries WHERE video_id=?', (ident,)).fetchone():
                active = con.execute("SELECT id FROM jobs WHERE kind='process' AND video_id=? AND status IN ('queued','running')", (ident,)).fetchone()
                if active:
                    job_ids.append(active['id'])
                else:
                    failed = con.execute("SELECT id FROM jobs WHERE kind='process' AND video_id=? AND status='failed' ORDER BY updated_at DESC LIMIT 1", (ident,)).fetchone()
                    job_id = failed['id'] if failed else uuid.uuid4().hex
                    if failed:
                        con.execute("UPDATE jobs SET status='queued',payload_json='{}',progress='Retry queued from Library',error=NULL,result_json=NULL,created_at=?,updated_at=? WHERE id=?", (now(), now(), job_id))
                    else:
                        con.execute("INSERT INTO jobs(id,kind,payload_json,video_id,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)", (job_id, 'process', '{}', ident, 'queued', now(), now()))
                    job_ids.append(job_id)
        if is_feed:
            # Saving is the explicit opt-in to future feed monitoring.
            con.execute('INSERT OR IGNORE INTO sources(id,url,since,next_scan,created_at) VALUES(?,?,?,?,?)',
                        (uuid.uuid4().hex, source_url, now(), now(), now()))
            con.execute('UPDATE sources SET enabled=1,next_scan=? WHERE url=?', (now(), source_url))
            source_id = con.execute('SELECT id FROM sources WHERE url=?', (source_url,)).fetchone()[0]
            for ident in ids:
                con.execute('INSERT OR IGNORE INTO source_videos VALUES(?,?,?)', (source_id, ident, now()))
            monitoring = bool(con.execute('SELECT enabled FROM sources WHERE url=?', (source_url,)).fetchone()[0])
    return {'job_ids': job_ids, 'saved': added, 'linked': linked, 'already_saved': len(ids) - linked, 'monitoring': monitoring if is_feed else False}


def delete_summary(video_id):
    """Remove a saved summary, retaining transcripts and feed deduplication history."""
    with connect() as con:
        con.execute('BEGIN IMMEDIATE')
        if con.execute("SELECT 1 FROM jobs WHERE video_id=? AND kind='process' AND status='running'", (video_id,)).fetchone():
            raise ValueError('This video is processing. Wait for it to finish before deleting its summary.')
        if not con.execute('SELECT 1 FROM library_items WHERE video_id=?', (video_id,)).fetchone():
            raise LookupError('Saved video not found.')
        con.execute("UPDATE jobs SET status='cancelled',result_json=NULL,progress='Summary deleted',updated_at=? WHERE video_id=? AND kind='process'", (now(), video_id))
        for table in ('mentions', 'summaries', 'library_items', 'library_links'):
            con.execute(f'DELETE FROM {table} WHERE video_id=?', (video_id,))
        con.execute("UPDATE videos SET category='other',updated_at=? WHERE id=?", (now(), video_id))
        search.sync(con, video_id)
    return {'ok': True}


def saved_sources(ids):
    if not ids:
        return {}
    with connect() as con:
        result = {}
        for row in con.execute('SELECT video_id,source_url FROM library_links WHERE video_id IN (' + ','.join('?' for _ in ids) + ')', ids):
            result.setdefault(row['video_id'], []).append(row['source_url'])
        return result
