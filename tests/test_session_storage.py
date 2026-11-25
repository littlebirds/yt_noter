import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from unittest.mock import patch

from app import config, db, feeds, server, worker
from test_app import DatabaseCase, ID, URL

OTHER = '12345678901'


class SessionStorageTests(DatabaseCase):
    def setUp(self):
        super().setUp()
        db.start_session()

    def tearDown(self):
        db.session_store.end()
        super().tearDown()

    def restart(self):
        db.session_store.end()
        db.init()
        db.start_session()

    def disk_count(self, table):
        with closing(sqlite3.connect(config.DB)) as con:
            return con.execute(f'SELECT count(*) FROM {table}').fetchone()[0]

    def draft(self, ident=ID):
        self.video(ident)
        job = worker.enqueue('process', {}, ident)
        db.save_transcript(ident, 'zh', 'test', [{'start': 42, 'text': '原文 NVDA'}])
        db.save_summary(ident, {'summary': '中文摘要', 'category': 'finance', 'mentions': [
            {'ticker':'NVDA','stance':'mention','evidence':'原文 NVDA','timestamp':42}]}, 'test')
        db.save_slides(ident, [{'start':10,'end':14,'timestamp':10,'image':b'jpeg','content_kind':'chart'}],4)
        db.save_summary_checkpoint(job, 'fingerprint','section 1',{'summary':'中文摘要'})
        return job

    def test_unsaved_research_and_checkpoints_never_reach_disk(self):
        job = self.draft()
        worker.progress(job, 'Processing')
        self.assertEqual(db.detail(ID)['summary']['markdown'], '中文摘要')
        self.assertEqual(db.summary_ids([ID]),{ID})
        self.assertEqual(db.summary_checkpoint(job,'fingerprint','section 1')['summary'],'中文摘要')
        self.assertEqual(json.loads(self.call('GET', f'/api/videos/{ID}')[1])['saved'],False)
        self.assertTrue(self.call('GET',f'/api/slides/{ID}/0')[0].startswith('200'))
        for table in ('videos','transcripts','summaries','slides','mentions','jobs','summary_checkpoints'):
            self.assertEqual(self.disk_count(table),0,table)
        old_session=db.session_store.session_id()
        self.restart()
        self.assertNotEqual(db.session_store.session_id(),old_session)
        self.assertIsNone(db.detail(ID))
        self.assertEqual(db.jobs(),[])
        self.assertEqual(db.summary_ids([ID]),set())
        self.assertTrue(self.call('GET',f'/api/jobs/{job}')[0].startswith('404'))
        self.assertNotEqual(worker.enqueue('process',{},ID),job)

    def test_save_during_processing_promotes_and_routes_later_writes(self):
        job=self.draft()
        self.assertEqual(worker.claim()['id'],job)
        saved=db.add_to_library([ID],URL,False)
        self.assertEqual(saved['saved'],1)
        worker.progress(job,'Continuing after save')
        db.save_summary(ID,{'summary':'新的中文摘要','category':'finance','mentions':[]},'test')
        db.save_summary_checkpoint(job,'fingerprint','section 2',{'summary':'第二段'})
        for table in ('videos','transcripts','summaries','slides','jobs'):
            self.assertEqual(self.disk_count(table),1,table)
        with db.connect(temporary=True) as con:
            self.assertEqual(con.execute('SELECT count(*) FROM videos').fetchone()[0],0)
            self.assertEqual(con.execute('SELECT count(*) FROM jobs').fetchone()[0],0)
        self.restart()
        video=db.detail(ID)
        self.assertTrue(video['saved'])
        self.assertEqual(video['summary']['markdown'],'新的中文摘要')
        self.assertEqual(video['slides'][0]['timestamp'],10)
        self.assertEqual(db.summary_checkpoint(job,'fingerprint','section 2')['summary'],'第二段')
        self.assertEqual(worker.claim()['id'],job)

    def test_promote_queued_job_before_metadata_exists(self):
        job=worker.enqueue('process',{},ID)
        db.add_to_library([ID],URL,False)
        self.assertEqual(worker.claim()['id'],job)
        self.assertEqual(self.disk_count('jobs'),1)
        self.video()
        self.assertTrue(db.detail(ID)['saved'])

    def test_promotion_failure_preserves_draft_and_rolls_back_disk(self):
        job=self.draft()
        with patch('app.search.sync',side_effect=RuntimeError('cannot index')):
            with self.assertRaises(RuntimeError):
                db.add_to_library([ID],URL,False)
        self.assertFalse(db.detail(ID)['saved'])
        self.assertEqual(self.disk_count('videos'),0)
        self.assertEqual(db.jobs()[0]['id'],job)
        self.assertEqual(db.detail(ID)['slides'][0]['content_kind'],'chart')

    def test_two_drafts_do_not_collide_on_mention_primary_keys(self):
        self.draft();db.add_to_library([ID],URL,False)
        self.draft(OTHER);db.add_to_library([OTHER],f'https://www.youtube.com/watch?v={OTHER}',False)
        self.assertEqual(self.disk_count('mentions'),2)
        self.restart()
        self.assertEqual(len(db.detail(ID)['mentions']),1)
        self.assertEqual(len(db.detail(OTHER)['mentions']),1)

    def test_discovery_is_temporary_but_its_selected_metadata_can_be_saved(self):
        job=worker.enqueue('discover',{'url':URL,'limit':1})
        with db.connect(job_id=job) as con:
            con.execute("UPDATE jobs SET status='done',result_json=? WHERE id=?",
                        (db.dumps({'videos':[{'id':ID,'title':'预览标题','creator':'作者'}]}),job))
        db.add_to_library([ID],URL,False)
        self.assertEqual(db.detail(ID)['title'],'预览标题')
        self.restart()
        self.assertEqual(db.detail(ID)['title'],'预览标题')
        self.assertFalse(any(j['kind']=='discover' for j in db.jobs()))

    def test_followed_feed_uploads_and_sync_jobs_survive_restart(self):
        self.video()
        source_url='https://www.youtube.com/@example/videos'
        saved=db.add_to_library([ID],source_url,True)
        feeds.schedule()
        kinds={job['kind'] for job in db.jobs()}
        self.assertIn('sync',kinds)
        self.assertIn('process',kinds)
        self.video(OTHER)
        db.mark_library(OTHER,source_url)
        worker.enqueue('process',{},OTHER)
        self.restart()
        self.assertEqual(len(feeds.sources()),1)
        self.assertTrue(db.detail(OTHER)['saved'])
        self.assertTrue(db.detail(ID)['saved'])
        self.assertIn(saved['job_ids'][0],[job['id'] for job in db.jobs()])
        self.assertTrue(any(job['kind']=='sync' for job in db.jobs()))

    def test_worker_processing_keeps_one_temporary_composite_job(self):
        self.video()
        db.save_transcript(ID,'zh','test',[{'start':42,'text':'原文'}])
        ident=worker.enqueue('process',{},ID)
        job=worker.claim()
        with patch('app.summarize.summarize',return_value={'summary':'中文摘要','category':'other','mentions':[]}), patch('app.slides.extract',return_value=[]):
            worker.process(job)
        self.assertEqual(db.detail(ID)['summary']['markdown'],'中文摘要')
        self.assertEqual([(j['id'],j['kind']) for j in db.jobs()],[(ident,'process')])
        self.assertIsNotNone(db.detail(ID)['slide_scan'])
        self.assertEqual(self.disk_count('jobs'),0)
        self.assertEqual(self.disk_count('summaries'),0)
        self.assertEqual(json.loads(self.call('GET',f'/api/jobs/{ident}')[1])['status'],'running')

    def test_single_queue_claims_across_both_stores_without_duplicates(self):
        ephemeral=worker.enqueue('process',{},ID)
        db.add_to_library([OTHER],f'https://www.youtube.com/watch?v={OTHER}',False)
        with ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(lambda _:worker.claim(),range(4)))
        ids=[r['id'] for r in results if r]
        self.assertEqual(len(ids),2)
        self.assertEqual(len(set(ids)),2)
        self.assertIn(ephemeral,ids)

    def test_removing_library_entry_keeps_transcript_only_for_current_session(self):
        self.draft();db.add_to_library([ID],URL,False)
        db.delete_summary(ID)
        self.assertFalse(db.detail(ID)['saved'])
        self.assertIsNotNone(db.detail(ID)['transcript'])
        self.assertEqual(self.disk_count('videos'),0)
        self.restart()
        self.assertIsNone(db.detail(ID))


class LegacyDraftMigrationTests(DatabaseCase):
    def tearDown(self):
        db.session_store.end()
        super().tearDown()

    def test_legacy_drafts_are_archived_once_saved_research_preserved(self):
        self.video()
        db.save_transcript(ID,'zh','test',[{'start':0,'text':'旧草稿'}])
        old=worker.enqueue('process',{},ID)
        self.video(OTHER)
        db.add_to_library([OTHER],f'https://www.youtube.com/watch?v={OTHER}',False)
        db.start_session()
        self.assertIsNone(db.detail(ID))
        self.assertTrue(db.detail(OTHER)['saved'])
        self.assertNotIn(old,[j['id'] for j in db.jobs()])
        archives=list(config.DATA.glob('*-legacy-drafts-*.sqlite3'))
        self.assertEqual(len(archives),1)
        with closing(sqlite3.connect(archives[0])) as con:
            self.assertEqual(con.execute('SELECT text FROM transcripts WHERE video_id=?',(ID,)).fetchone()[0],'旧草稿')
        db.session_store.end();db.init();db.start_session()
        self.assertEqual(len(list(config.DATA.glob('*-legacy-drafts-*.sqlite3'))),1)
