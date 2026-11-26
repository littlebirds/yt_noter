"""Serial inference jobs plus an independent interactive discovery worker."""
import json
import logging
import threading
import uuid
from . import config, db, media, summarize, feeds, llm_settings, slides, storage

log = logging.getLogger(__name__)
STOP = threading.Event()


def enqueue(kind, payload, video_id=None):
    if kind == 'slides':
        kind, payload = 'process', {**payload, 'force_slides': True}
    if kind not in {'discover', 'process', 'sync'}:
        raise ValueError('This job type is no longer supported.')
    with db.connect(video_id=video_id, temporary=kind == 'discover') as con:
        storage.begin_write(con)
        if video_id:
            existing = con.execute("SELECT id FROM jobs WHERE kind=? AND video_id=? AND status IN ('queued','running')",
                                   (kind, video_id)).fetchone()
            if existing:
                return existing['id']
        if kind == 'process' and not payload.get('force') and not payload.get('force_slides') and db.research_ready(con, video_id):
            completed = con.execute("SELECT id FROM jobs WHERE kind='process' AND video_id=? AND status='done' ORDER BY created_at DESC LIMIT 1", (video_id,)).fetchone()
            if completed:
                return completed['id']
            ident = uuid.uuid4().hex
            con.execute('''INSERT INTO jobs(id,kind,payload_json,video_id,status,progress,result_json,created_at,updated_at)
                           VALUES(?,?,?,?,?,?,?,?,?)''', (ident, kind, db.dumps(payload), video_id, 'done',
                           'Reused existing research', db.dumps({'video_id': video_id, 'reused': True}), db.now(), db.now()))
            return ident
        ident = uuid.uuid4().hex
        con.execute('''INSERT INTO jobs(id,kind,payload_json,video_id,status,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?)''', (ident, kind, db.dumps(payload), video_id, 'queued', db.now(), db.now()))
        return ident


def retry(job_id):
    """Retry in place; concurrent clicks and historical cards share one active job."""
    with db.connect(job_id=job_id) as con:
        storage.begin_write(con)
        row = con.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        if not row:
            raise LookupError('Job not found.')
        if row['status'] in ('queued', 'running'):
            return row['id']
        if row['status'] != 'failed':
            raise ValueError('Only failed jobs can be retried.')
        if row['kind'] == 'process':
            active = con.execute("SELECT id FROM jobs WHERE kind='process' AND video_id=? AND status IN ('queued','running')", (row['video_id'],)).fetchone()
            if active:
                return active['id']
            if not json.loads(row['payload_json']).get('force') and db.research_ready(con, row['video_id']) and not json.loads(row['payload_json']).get('force_slides'):
                con.execute("UPDATE jobs SET status='done',error=NULL,progress='Reused existing research',result_json=?,updated_at=? WHERE id=?",
                            (db.dumps({'video_id': row['video_id'], 'reused': True}), db.now(), job_id))
                return job_id
        elif row['kind'] == 'slides':
            active = con.execute("SELECT id FROM jobs WHERE kind='slides' AND video_id=? AND status IN ('queued','running')", (row['video_id'],)).fetchone()
            if active:
                return active['id']
        elif row['kind'] == 'sync':
            active = con.execute(f"SELECT id FROM jobs WHERE kind='sync' AND status IN ('queued','running') AND {storage.job_source(con)}=?", (json.loads(row['payload_json'])['source_id'],)).fetchone()
            if active:
                return active['id']
        if row['kind'] not in {'process', 'sync', 'discover', 'slides'}:
            raise ValueError('This job type is no longer supported.')
        con.execute("UPDATE jobs SET status='queued',progress='Retry queued',error=NULL,result_json=NULL,created_at=?,updated_at=? WHERE id=?", (db.now(), db.now(), job_id))
        return job_id


def progress(job_id, message):
    with db.connect(job_id=job_id) as con:
        con.execute('UPDATE jobs SET progress=?,updated_at=? WHERE id=?', (message, db.now(), job_id))



def process(job):
    payload = json.loads(job['payload_json'])
    update = lambda message: progress(job['id'], message)
    if job['kind'] == 'sync':
        return feeds.sync(payload['source_id'], update)
    if job['kind'] == 'discover':
        result = media.discover(payload['url'], payload['limit'], update, offset=payload.get('offset', 0))
        ready = db.summary_ids([v['id'] for v in result['videos']])
        saved = db.saved_sources([v['id'] for v in result['videos']])
        for video in result['videos']:
            video['saved_sources'] = saved.get(video['id'], [])
            video['summary_ready'] = video['id'] in ready
        return result
    video_id = job['video_id']
    if job['kind'] in ('process', 'slides'):
        video = db.detail(video_id)
        expected = json.loads(video['metadata_json']).get('language') if video else None
        repair_language = bool(video and video['transcript'] and expected and
                               video['transcript']['language'].split('-')[0] != expected.split('-')[0])
        stage = lambda name: lambda message: update(f'{name}: {message}')
        if not video or not video['transcript'] or repair_language:
            update('1/3 Transcript: Reading video metadata')
            info = media.video_info(f'https://www.youtube.com/watch?v={video_id}')
            db.save_video(media.metadata(info))
            language, source, segments = media.transcribe(info, stage('1/3 Transcript'))
            db.save_transcript(video_id, language, source, segments)
            video = db.detail(video_id)
        else:
            update('1/3 Transcript: Reusing saved transcript')
        regenerate = payload.get('force') and not payload.get('_summary_complete')
        if not video['summary'] or repair_language or regenerate:
            transcript = video['transcript']
            settings = llm_settings.load()
            with llm_settings.use(settings):
                result = summarize.summarize(transcript['language'], transcript['segments'],
                                             stage('2/3 Summary'), checkpoint_job=job['id'])
            db.save_summary(video_id, result, settings['model'])
            payload['_summary_complete'] = True
            with db.connect(job_id=job['id']) as con:
                con.execute('UPDATE jobs SET payload_json=? WHERE id=?', (db.dumps(payload),job['id']))
        else:
            update('2/3 Summary: Reusing saved summary')
        if not video['slide_scan'] or payload.get('force_slides') or job['kind']=='slides':
            minimum = payload.get('minimum', 4)
            update('3/3 Slides: Starting scan')
            result = slides.extract(video_id, minimum, stage('3/3 Slides'))
            db.save_slides(video_id, result, minimum)
            count = len(result)
        else:
            update('3/3 Slides: Reusing saved slides')
            count = len(video['slides'])
        return {'video_id': video_id, 'slides': count, 'reused': bool(video['summary'] and not regenerate and video['slide_scan'] and not payload.get('force_slides') and job['kind'] != 'slides')}
    raise ValueError('Unknown job kind.')


@db.session_store.locked
def claim(discovery=False):
    # Both stores share the same serial processing lane and atomic promotion lock.
    condition = "kind='discover'" if discovery else "kind<>'discover'"
    candidates = []
    for temporary in db.stores():
        with db.connect(temporary=temporary) as con:
            row = con.execute("SELECT * FROM jobs WHERE status='queued' AND " + condition +
                              ' ORDER BY created_at,id LIMIT 1').fetchone()
            if row:
                candidates.append(dict(row))
    if not candidates:
        return None
    row = min(candidates, key=lambda j: (j['created_at'],j['id']))
    with db.connect(job_id=row['id']) as con:
        con.execute("UPDATE jobs SET status='running',progress='Starting',updated_at=? WHERE id=?",
                    (db.now(),row['id']))
    return row


def run(discovery=False):
    while not STOP.is_set():
        if not discovery:
            feeds.schedule()
        row = claim(discovery)
        if not row:
            STOP.wait(1)
            continue
        try:
            result = process(dict(row))
            with db.connect(job_id=row['id']) as con:
                con.execute("UPDATE jobs SET status='done',progress='Complete',result_json=?,updated_at=? WHERE id=?",
                            (db.dumps(result), db.now(), row['id']))
        except Exception as exc:
            log.exception('Job %s (%s, video=%s) failed', row['id'], row['kind'], row.get('video_id'))
            # Expected actionable errors are safe; library exception details may contain signed URLs.
            message = str(exc) if isinstance(exc, (RuntimeError, ValueError)) else f'{type(exc).__name__} during processing. See the server terminal traceback for job {row["id"]}.'
            with db.connect(job_id=row['id']) as con:
                con.execute("UPDATE jobs SET status='failed',error=?,updated_at=? WHERE id=?", (message, db.now(), row['id']))
