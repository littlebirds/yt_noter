import json
from unittest.mock import patch
from app import db, worker
from test_app import DatabaseCase, ID


class CompositeJobTests(DatabaseCase):
    def setup_transcript(self):
        self.video()
        db.save_transcript(ID,'zh','test',[{'start':42,'text':'中文原文'}])

    def test_summary_and_slides_finish_as_one_job(self):
        self.setup_transcript()
        ident=worker.enqueue('process',{},ID)
        job=worker.claim()
        progress=[]
        with patch('app.summarize.summarize',return_value={'summary':'中文摘要','category':'other','mentions':[]}), patch('app.slides.extract',return_value=[]) as scan, patch('app.worker.progress',side_effect=lambda _,text:progress.append(text)):
            result=worker.process(job)
        self.assertEqual(result['slides'],0)
        self.assertEqual([j['id'] for j in db.jobs()],[ident])
        self.assertIsNotNone(db.detail(ID)['summary'])
        self.assertIsNotNone(db.detail(ID)['slide_scan'])
        self.assertTrue(any(p.startswith('3/3 Slides') for p in progress))
        scan.assert_called_once()
        self.assertIsNone(worker.claim())

    def test_slide_failure_retry_keeps_completed_forced_summary(self):
        self.setup_transcript()
        ident=worker.enqueue('process',{'force':True},ID)
        job=worker.claim()
        with patch('app.summarize.summarize',return_value={'summary':'中文摘要','category':'other','mentions':[]}), patch('app.slides.extract',side_effect=RuntimeError('decode failed')):
            with self.assertRaises(RuntimeError):
                worker.process(job)
        with db.connect() as con:
            con.execute("UPDATE jobs SET status='failed' WHERE id=?",(ident,))
        self.assertEqual(worker.retry(ident),ident)
        retry=worker.claim()
        with patch('app.summarize.summarize') as inference,patch('app.media.transcribe') as transcribe,patch('app.slides.extract',return_value=[]):
            worker.process(retry)
        inference.assert_not_called();transcribe.assert_not_called()
        self.assertIsNotNone(db.detail(ID)['slide_scan'])

    def test_existing_summary_without_slides_is_not_treated_as_complete(self):
        self.setup_transcript()
        db.save_summary(ID,{'summary':'已有摘要','category':'other','mentions':[]},'test')
        ident=worker.enqueue('process',{},ID)
        self.assertEqual(worker.enqueue('process',{},ID),ident)
        with patch('app.summarize.summarize') as inference,patch('app.slides.extract',return_value=[]):
            worker.process(worker.claim())
        inference.assert_not_called()
        with db.connect() as con:
            con.execute("UPDATE jobs SET status='done' WHERE id=?",(ident,))
        self.assertEqual(worker.enqueue('process',{},ID),ident)

    def test_manual_rescan_reuses_summary_and_has_same_job_type(self):
        self.setup_transcript()
        db.save_summary(ID,{'summary':'已有摘要','category':'other','mentions':[]},'test')
        db.save_slides(ID,[],4)
        ident=worker.enqueue('slides',{'minimum':6},ID)
        job=worker.claim()
        self.assertEqual(job['kind'],'process')
        with patch('app.summarize.summarize') as inference,patch('app.slides.extract',return_value=[]) as scan:
            worker.process(job)
        inference.assert_not_called()
        self.assertEqual(scan.call_args.args[1],6)
        self.assertEqual(len(db.jobs()),1)

    def test_migration_folds_pending_slide_job_into_existing_video_job(self):
        self.setup_transcript()
        main=worker.enqueue('process',{},ID)
        with db.connect() as con:
            con.execute('DELETE FROM schema_version WHERE version=10')
            con.execute('INSERT INTO jobs(id,kind,payload_json,video_id,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',
                        ('old-slide','slides','{"minimum":6}',ID,'queued',db.now(),db.now()))
        db.init()
        active=[j for j in db.jobs() if j['status'] in ('queued','running')]
        self.assertEqual([j['id'] for j in active],[main])
        self.assertTrue(json.loads(active[0]['payload_json'])['force_slides'])
        self.assertEqual(json.loads(active[0]['payload_json'])['minimum'],6)
