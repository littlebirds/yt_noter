import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from app import config, db, feeds, media, server, summarize, worker

ID = 'abcdefghijk'
URL = f'https://www.youtube.com/watch?v={ID}'


class DatabaseCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.saved = config.DATA, config.DB
        config.DATA = Path(self.temp.name)
        config.DB = config.DATA / 'test.sqlite3'
        db.init()

    def tearDown(self):
        config.DATA, config.DB = self.saved
        self.temp.cleanup()

    def video(self, ident=ID, published='2026-01-01T12:00:00+00:00'):
        v = {'id': ident, 'url': f'https://www.youtube.com/watch?v={ident}', 'title': '半导体研究',
             'creator': '研究者', 'published_at': published, 'duration': 300}
        db.save_video(v)
        return v

    def call(self, method, path, body=None, token='', origin=None):
        payload = json.dumps(body).encode() if body is not None else b''
        env = {'REQUEST_METHOD': method, 'PATH_INFO': path, 'QUERY_STRING': '',
               'wsgi.input': io.BytesIO(payload), 'CONTENT_TYPE': 'application/json',
               'CONTENT_LENGTH': str(len(payload)), 'HTTP_HOST': 'localhost:8000',
               'HTTP_AUTHORIZATION': token}
        if origin:
            env['HTTP_ORIGIN'] = origin
        status = []
        result = b''.join(server.application(env, lambda s, h: status.append(s)))
        return status[0], result


class PersistenceTests(DatabaseCase):
    def test_chinese_search_and_filters(self):
        self.video()
        db.save_transcript(ID, 'zh', 'youtube_original_captions', [{'start': 4, 'end': 10, 'text': 'NVDA 的增长和估值风险'}])
        db.save_summary(ID, {'summary': '## 要点\n增长与风险', 'category': 'finance', 'mentions': [
            {'ticker': 'NVDA', 'stance': 'mention', 'evidence': 'NVDA 的增长', 'timestamp': 4}]}, 'test')
        db.add_to_library([ID], URL, False)
        self.assertEqual(len(db.library({'q': '估值', 'ticker': 'nvda', 'creator': '研究者', 'from': '2025-01-01'})['videos']), 1)
        self.assertEqual(db.library({'ticker': 'AMD'})['videos'], [])
        self.assertEqual(db.library({'from': '2027-01-01'})['videos'], [])
        self.assertEqual(db.library({'q': '%'})['videos'], [])
        db.init()
        self.assertEqual(db.detail(ID)['transcript']['language'], 'zh')

    def test_discovery_worker_bypasses_processing_backlog(self):
        processing = worker.enqueue('process', {}, ID)
        lookup = worker.enqueue('discover', {'url': URL, 'limit': 1})
        self.assertEqual(worker.claim(discovery=True)['id'], lookup)
        self.assertIsNone(worker.claim(discovery=True))
        self.assertEqual(worker.claim()['id'], processing)
        self.assertIsNone(worker.claim())

    def test_processing_worker_does_not_claim_interactive_lookup(self):
        worker.enqueue('discover', {'url': URL, 'limit': 1})
        self.assertIsNone(worker.claim())
        self.assertEqual(db.jobs()[0]['status'], 'queued')

    def test_jobs_deduplicate_and_resume(self):
        first = worker.enqueue('process', {}, ID)
        self.assertEqual(worker.enqueue('process', {}, ID), first)
        with db.connect() as con:
            con.execute("UPDATE jobs SET status='running'")
        db.init()
        self.assertEqual(db.jobs()[0]['status'], 'queued')

    def test_summary_failure_preserves_transcript(self):
        self.video()
        db.save_transcript(ID, 'zh', 'test', [{'start': 0, 'end': 1, 'text': '原文'}])
        with patch('app.summarize.summarize', side_effect=RuntimeError('Inference offline')):
            with self.assertRaises(RuntimeError):
                worker.process({'id':'job', 'kind':'process', 'payload_json':'{}', 'video_id':ID})
        self.assertEqual(db.detail(ID)['transcript']['text'], '原文')

    def test_export_preserves_transcript_and_source_timestamp(self):
        self.video()
        db.save_transcript(ID, 'zh', 'test', [{'start': 12, 'end': 20, 'text': '估值与风险'}])
        exported = server.export_markdown(db.detail(ID))
        self.assertIn('&t=12s', exported)
        self.assertIn('估值与风险', exported)
        self.assertNotIn('data:image', exported)
        self.assertNotIn('assets', db.detail(ID))

    def test_removed_capture_jobs_are_retired_on_restart(self):
        with db.connect() as con:
            # Simulate records from an older installation; preserve its stored media metadata.
            con.execute('CREATE TABLE assets(id TEXT PRIMARY KEY, filename TEXT)')
            con.execute("INSERT INTO assets VALUES('old', 'old.jpg')")
            for status in ('queued', 'running', 'failed'):
                con.execute('INSERT INTO jobs(id,kind,payload_json,status,created_at,updated_at) VALUES(?,?,?,?,?,?)',
                            (status, 'frame', '{}', status, db.now(), db.now()))
        db.init()
        self.assertTrue(all(j['status'] == 'cancelled' for j in db.jobs()))
        with db.connect() as con:
            self.assertEqual(con.execute('SELECT filename FROM assets').fetchone()[0], 'old.jpg')
        with self.assertRaises(ValueError):
            worker.enqueue('frame', {}, ID)


class LibrarySaveTests(DatabaseCase):
    def summarize_video(self, ident=ID):
        self.video(ident)
        db.save_transcript(ident, 'zh', 'test', [{'start':0,'end':2,'text':'研究原文'}])
        db.save_summary(ident, {'summary':'中文总结','category':'finance','mentions':[]}, 'test-model')

    def test_processing_drafts_are_not_in_library_until_saved(self):
        self.summarize_video()
        self.assertEqual(db.library({})['stats']['videos'], 0)
        self.assertEqual(db.library({})['creators'], [])
        self.assertIsNotNone(db.detail(ID)['summary'])
        status, _ = self.call('POST', '/api/library', {'ids':[ID], 'url':URL})
        self.assertTrue(status.startswith('200'))
        self.assertEqual(db.library({})['stats']['summaries'], 1)
        self.assertEqual(db.detail(ID)['source_urls'], [URL])
        self.assertEqual(feeds.sources(), [])

    def test_saving_feed_links_selected_results_and_subscribes_once(self):
        self.summarize_video()
        self.summarize_video('12345678901')
        for _ in range(2):
            status, _ = self.call('POST', '/api/library', {'ids':[ID], 'url':'https://www.youtube.com/@YHGJ9527'})
            self.assertTrue(status.startswith('200'))
        self.assertEqual(db.library({})['stats']['videos'], 1)
        self.assertEqual(db.library({})['stats']['transcripts'], 1)
        self.assertEqual(len(feeds.sources()), 1)
        self.assertEqual(db.detail(ID)['source_urls'], ['https://www.youtube.com/@YHGJ9527/videos'])
        self.assertFalse(db.detail('12345678901')['saved'])

    def test_selection_can_be_saved_without_summaries(self):
        status, _ = self.call('POST','/api/library',{'ids':[ID,'12345678901'],'url':'https://www.youtube.com/@research'})
        self.assertTrue(status.startswith('200'))
        self.assertEqual(db.library({})['stats']['videos'], 2)
        self.assertEqual(db.library({})['stats']['summaries'], 0)
        self.assertEqual(len(feeds.sources()), 1)
        self.assertTrue(feeds.sources()[0]['enabled'])
        self.assertEqual(len(db.jobs()), 2)
        self.assertTrue(all(j['status'] == 'queued' for j in db.jobs()))
        self.assertIsNone(db.detail(ID)['summary'])
        self.assertEqual(db.detail(ID)['url'], URL)

    def test_saved_video_link_must_match_selection(self):
        self.summarize_video()
        status, _ = self.call('POST','/api/library',{'ids':[ID],'url':'https://youtu.be/12345678901'})
        self.assertTrue(status.startswith('400'))

    def test_migration_preserves_old_library_once_without_promoting_new_drafts(self):
        self.summarize_video()
        with db.connect() as con:
            con.execute('DELETE FROM schema_version WHERE version=3')
        db.init()
        self.assertTrue(db.detail(ID)['saved'])
        self.summarize_video('12345678901')
        db.init()
        self.assertFalse(db.detail('12345678901')['saved'])
        self.assertEqual(db.library({})['stats']['videos'], 1)


class MediaTests(unittest.TestCase):
    def test_url_validation_and_posts_normalization(self):
        self.assertEqual(media.canonical_url('https://www.youtube.com/@YHGJ9527/posts'), ('https://www.youtube.com/@YHGJ9527/videos', True))
        self.assertEqual(media.canonical_url(f'https://youtu.be/{ID}?t=3'), (URL, False))
        for url in ['http://localhost/a', 'https://youtube.com.evil.com/watch?v='+ID, 'https://youtube.com:123/watch?v='+ID, 'file:///etc/passwd', 'https://evil@youtube.com/watch?v='+ID]:
            with self.assertRaises(ValueError):
                media.canonical_url(url)

    def test_reject_translation_even_if_english_metadata(self):
        native = {'ext': 'json3', 'url': 'https://www.youtube.com/api/timedtext?lang=zh-Hans'}
        translated = {'ext': 'json3', 'url': 'https://www.youtube.com/api/timedtext?lang=zh-Hans&tlang=en'}
        result = media.native_track({'language':'en', 'formats':[{'acodec':'opus','language':'zh-Hans','language_preference':10}], 'automatic_captions':{'zh-Hans-orig':[native], 'en':[translated]}})
        self.assertEqual(result[0], 'zh-Hans')
        self.assertEqual(result[1], native['url'])

    def test_unknown_original_does_not_choose_english(self):
        self.assertIsNone(media.native_track({'subtitles':{'en':[{'ext':'json3','url':'https://www.youtube.com/api/timedtext?lang=en'}]}}))
        self.assertIsNone(media.native_track({'language':'zh','automatic_captions':{'zh':[{'ext':'json3','url':'https://www.youtube.com/api/timedtext?lang=zh&tlang=en'}]}}))

    def test_original_audio_selected_over_higher_bitrate_dub(self):
        info = {'formats': [
            {'format_id':'dub','acodec':'opus','vcodec':'none','language':'en','language_preference':5,'abr':200},
            {'format_id':'orig','acodec':'opus','vcodec':'none','language':'zh','language_preference':10,'abr':100}]}
        self.assertEqual(media.audio_format(info), 'orig')
        self.assertEqual(media.original_language(info), 'zh')

    def test_ambiguous_multilingual_audio_rejected(self):
        with self.assertRaises(RuntimeError):
            media.audio_format({'formats': [
                {'acodec':'opus','language':'en'}, {'acodec':'opus','language':'zh'}]})

    def test_discovery_paginates_and_retains_failed_entries(self):
        entries = [{'id': ID, 'title': 'Needs lookup'}, {'id': '12345678901', 'upload_date':'20260101'}]
        with patch('app.media.run_ytdlp', return_value=json.dumps({'entries':entries})) as call, patch('app.media.video_info', side_effect=RuntimeError('YouTube rate-limited the request (HTTP 429).')):
            result = media.discover('https://www.youtube.com/@test/videos', 2, offset=50)
        args = call.call_args.args[0]
        self.assertEqual(args[args.index('--playlist-start')+1], '51')
        self.assertEqual(args[args.index('--playlist-end')+1], '52')
        self.assertEqual(len(result['videos']), 2)
        self.assertEqual(result['next_offset'], 52)
        self.assertTrue(result['possibly_more'])
        failed = next(v for v in result['videos'] if v['id'] == ID)
        self.assertIsNone(failed['published_at'])
        self.assertIn('429', failed['metadata_error'])
        self.assertIn('429', result['warnings'][0])

    def test_metadata_lookup_tolerates_missing_formats(self):
        with patch('app.media.run_ytdlp', return_value=json.dumps({'id': ID})) as call:
            media.video_info(URL)
        self.assertIn('--ignore-no-formats-error', call.call_args.args[0])

    def test_caption_timing(self):
        result = media.parse_json3({'events':[{'tStartMs':1500,'dDurationMs':2000,'segs':[{'utf8':'中文 &amp; English'}]}]})
        self.assertEqual(result, [{'start':1.5,'end':3.5,'text':'中文 & English'}])

    def test_metadata_unknown_date_stays_unknown(self):
        self.assertIsNone(media.metadata({'id':ID})['published_at'])


class InferenceAdapterTests(unittest.TestCase):
    def response(self, content='{"summary":"中文摘要"}', reason='stop'):
        return io.BytesIO(json.dumps({'choices': [{'finish_reason': reason, 'message': {'content': content}}]}).encode())

    def test_llama_chat_request_and_response(self):
        with patch('app.summarize.urlopen', return_value=self.response()) as call:
            result = summarize.chat('System prompt', '中文原文')
        self.assertEqual(result['summary'], '中文摘要')
        request = call.call_args.args[0]
        self.assertEqual(request.full_url, config.LLM_URL + '/v1/chat/completions')
        payload = json.loads(request.data)
        self.assertEqual(payload['model'], config.LLM_MODEL)
        self.assertEqual(payload['response_format']['type'], 'json_object')
        self.assertEqual(payload['response_format']['schema']['required'], ['summary'])
        self.assertEqual(payload['max_tokens'], 2200)
        self.assertFalse(payload['stream'])
        self.assertNotIn('options', payload)
        self.assertNotIn('keep_alive', payload)

    def test_truncated_output_is_rejected(self):
        with patch('app.summarize.urlopen', return_value=self.response(reason='length')):
            with self.assertRaisesRegex(RuntimeError, 'truncated'):
                summarize.chat('system', 'text')

    def test_invalid_response_envelopes_are_reported(self):
        for output in ({'choices': []}, {'choices': [{'message': {'content': None}}]}, {'choices': None}):
            with patch('app.summarize.urlopen', return_value=io.BytesIO(json.dumps(output).encode())):
                with self.assertRaisesRegex(RuntimeError, 'invalid JSON'):
                    summarize.chat('system', 'text')

    def test_unavailable_server_has_actionable_error(self):
        from urllib.error import URLError
        with patch('app.summarize.urlopen', side_effect=URLError('offline')):
            with self.assertRaisesRegex(RuntimeError, 'Inference API is unavailable'):
                summarize.chat('system', 'text')

    def test_status_requires_matching_model_alias(self):
        for alias, ready in ((config.LLM_MODEL, True), ('different-model', False)):
            response = io.BytesIO(json.dumps({'data': [{'id': alias}]}).encode())
            with patch('app.server.urlopen', return_value=response) as call:
                status = server.api('GET', '/api/status', {}, {})
            self.assertEqual(call.call_args.args[0].full_url, config.LLM_URL + '/v1/models')
            self.assertTrue(status['inference'])
            self.assertEqual(status['model_ready'], ready)
            self.assertEqual(status['engine'], 'llama.cpp')


class SummaryTests(unittest.TestCase):
    def test_chunking_keeps_all_characters(self):
        source = [{'start':0,'end':10,'text':'中'*14000}]
        result = list(summarize.chunks(source))
        self.assertTrue(all(sum(len(s['text']) for s in chunk)<=2400 for chunk in result))
        self.assertEqual(''.join(s['text'] for chunk in result for s in chunk), source[0]['text'])

    def test_evidence_and_timestamp_are_verified(self):
        result = {'mentions':[{'ticker':'NVDA','stance':'buy','evidence':'NVDA值得关注'},
                              {'ticker':'AMD','evidence':'不存在的推荐'}, {'ticker':'TSLA','evidence':'NVDA值得关注'}]}
        output = summarize.checked_mentions(result, [{'start':42,'end':48,'text':'我认为NVDA值得关注'}])
        self.assertEqual(len(output), 1)
        self.assertEqual(output[0]['timestamp'], 42)

    def test_chinese_translation_rejected(self):
        with self.assertRaises(RuntimeError):
            summarize.checked_summary({'summary':'English translation only'}, 'zh-Hans')

    def test_all_summary_chunks_are_reduced(self):
        responses = [{'summary':'第一段摘要','category':'finance','mentions':[]},
                     {'summary':'第二段摘要','category':'finance','mentions':[]},
                     {'summary':'合并摘要'}]
        with patch('app.summarize.chat', side_effect=responses) as chat:
            result = summarize.summarize('zh', [{'start':0,'end':1,'text':'中'*1200}])
        self.assertEqual(chat.call_count, 3)
        self.assertEqual(result['summary'], '合并摘要')


class ApiTests(DatabaseCase):
    def test_auth_and_health(self):
        with patch('app.server.TOKEN', 'secret'):
            self.assertTrue(self.call('GET','/api/library')[0].startswith('401'))
            self.assertTrue(self.call('GET','/api/library',token='Bearer secret')[0].startswith('200'))
            self.assertTrue(self.call('GET','/healthz')[0].startswith('200'))

    def test_invalid_requests(self):
        for body in [{'ids':[]}, {'ids':['invalid']}, {'ids':'abcdefghijk'}]:
            self.assertTrue(self.call('POST','/api/process',body)[0].startswith('400'))
        self.assertTrue(self.call('POST','/api/process',{'ids':[ID]},origin='https://evil.example')[0].startswith('403'))
        self.assertTrue(self.call('GET','/assets/../../secret')[0].startswith('404'))

    def test_queue_and_poll_api(self):
        status, body = self.call('POST','/api/process',{'ids':[ID,ID]})
        self.assertTrue(status.startswith('200'))
        self.assertEqual(len(json.loads(body)['job_ids']), 1)
        self.assertEqual(len(json.loads(self.call('GET','/api/jobs')[1])['jobs']), 1)

    def test_static_html(self):
        status, body = self.call('GET','/')
        self.assertTrue(status.startswith('200'))
        self.assertIn(b'selection-count', body)
        self.assertNotIn(b'go-back-unit', body)
        self.assertNotIn(b'datetime-local', body)
        self.assertNotIn(b'window-to', body)
        self.assertNotIn(b'data-tab="frames"', body)
        self.assertNotIn(b'aboutImages', body)

    def test_capture_endpoints_are_removed(self):
        self.assertTrue(self.call('POST','/api/frame',{'video_id':ID,'seconds':12})[0].startswith('404'))
        self.assertTrue(self.call('GET','/assets/' + 'a'*32 + '.jpg')[0].startswith('404'))

    def test_more_pages_do_not_create_or_modify_subscription(self):
        with patch('app.feeds.subscribe') as subscribe:
            status, body = self.call('POST', '/api/discover', {'url':'https://www.youtube.com/@test/videos', 'limit':50, 'offset':50})
        self.assertTrue(status.startswith('200'), body)
        subscribe.assert_not_called()
        self.assertEqual(json.loads(db.jobs()[0]['payload_json'])['offset'], 50)
        self.assertTrue(self.call('POST','/api/discover',{'url':URL,'offset':-1})[0].startswith('400'))

    def test_feed_discovery_does_not_subscribe(self):
        status, body = self.call('POST','/api/discover',{'url':'https://www.youtube.com/@YHGJ9527/posts','since':'2025-01-01T00:00:00Z'})
        self.assertTrue(status.startswith('200'), body)
        self.assertEqual(feeds.sources(), [])


class FeedTests(DatabaseCase):
    def subscribe(self):
        return feeds.subscribe('https://www.youtube.com/@research/videos', '2025-01-01T00:00:00+00:00')

    def test_restart_deduplicates_subscription_and_pending_scan(self):
        source_id=self.subscribe()
        self.assertEqual(self.subscribe(),source_id)
        feeds.schedule(); feeds.schedule(); db.init(); feeds.schedule()
        self.assertEqual(len(db.jobs()),1)

    def test_backfill_and_new_upload_after_restart(self):
        source_id=self.subscribe()
        result={'videos':[self.video()], 'warnings':[], 'possibly_more':False,'scanned':1}
        with patch('app.media.discover',return_value=result):
            feeds.sync(source_id,lambda _:None)
            feeds.sync(source_id,lambda _:None)
        self.assertEqual(len([j for j in db.jobs() if j['kind']=='process']),1)
        db.init()
        new=self.video('12345678901',datetime.now(timezone.utc).isoformat())
        with patch('app.media.discover',return_value={**result,'videos':[new]}):
            feeds.sync(source_id,lambda _:None)
        self.assertEqual(len([j for j in db.jobs() if j['kind']=='process']),2)
        self.assertEqual(feeds.sources()[0]['discovered'],2)

    def test_pagination_continues_beyond_preview_limit(self):
        source_id=self.subscribe()
        result={'videos':[self.video()], 'warnings':[], 'possibly_more':True,'scanned':25}
        with patch('app.media.discover',return_value=result):
            feeds.sync(source_id,lambda _:None)
        source=feeds.sources()[0]
        self.assertEqual(source['cursor'],20)
        self.assertIsNone(source['checkpoint'])
        self.assertIsNotNone(source['scan_started'])

    def test_partial_metadata_failure_still_queues_readable_videos(self):
        ident = self.subscribe()
        result = {'videos':[self.video(), {'id':'12345678901','published_at':None,'metadata_error':'offline'}], 'warnings':['offline']}
        with patch('app.media.discover', return_value=result):
            with self.assertRaisesRegex(RuntimeError, 'Readable videos were queued'):
                feeds.sync(ident, lambda _:None)
        self.assertEqual(len([j for j in db.jobs() if j['kind']=='process']), 1)
        self.assertIsNone(feeds.sources()[0]['checkpoint'])

    def test_errors_do_not_advance_checkpoint(self):
        source_id=self.subscribe()
        with patch('app.media.discover',return_value={'videos':[], 'warnings':['error']}):
            with self.assertRaises(RuntimeError):
                feeds.sync(source_id,lambda _:None)
        source=feeds.sources()[0]
        self.assertEqual(source['cursor'],0)
        self.assertIsNone(source['checkpoint'])
        self.assertIsNotNone(source['last_error'])

    def test_pause_and_resume(self):
        source_id=self.subscribe()
        feeds.update_source(source_id,False);feeds.schedule()
        self.assertEqual(db.jobs(),[])
        feeds.update_source(source_id,True);feeds.schedule()
        self.assertEqual(len(db.jobs()),1)

    def test_extending_history_resets_checkpoint(self):
        source_id=self.subscribe()
        with db.connect() as con:
            con.execute('UPDATE sources SET checkpoint=? WHERE id=?',(db.now(),source_id))
        feeds.subscribe('https://www.youtube.com/@research/videos','2020-01-01T00:00:00Z')
        self.assertIsNone(feeds.sources()[0]['checkpoint'])

    def test_day_only_upload_does_not_get_lost_at_intraday_boundary(self):
        midnight = datetime.now(timezone.utc).replace(hour=0,minute=0,second=0,microsecond=0)
        ident = feeds.subscribe('https://www.youtube.com/@today/videos', db.now())
        video = self.video(published=midnight.isoformat())
        video['metadata'] = {'date_precision':'day'}
        with patch('app.media.discover', return_value={'videos':[video], 'warnings':[], 'possibly_more':False}):
            feeds.sync(ident, lambda _:None)
        self.assertEqual(len([j for j in db.jobs() if j['kind']=='process']), 1)

    def test_invalid_dates(self):
        for value in ['bad','2025-01-01T00:00:00', (datetime.now(timezone.utc)+timedelta(days=1)).isoformat()]:
            with self.assertRaises(ValueError):
                feeds.timestamp(value)


if __name__ == '__main__':
    unittest.main()


class LibraryManagementTests(DatabaseCase):
    def save(self, ident=ID, source=URL, feed=False):
        self.video(ident)
        db.save_transcript(ident, 'zh', 'captions', [{'start': 0, 'text': 'NVDA'}])
        db.save_summary(ident, {'summary': '研究总结', 'category': 'finance', 'mentions': [
            {'ticker': 'NVDA', 'stance': 'mention', 'evidence': 'NVDA', 'timestamp': 0}]}, 'test')
        db.add_to_library([ident], source, feed)

    def test_grouped_and_ungrouped_with_filters(self):
        feed = 'https://www.youtube.com/@example/videos'
        self.save(source=feed, feed=True)
        self.save('12345678901', 'https://www.youtube.com/watch?v=12345678901')
        result = db.library({})
        self.assertEqual(len(result['videos']), 2)
        self.assertEqual(result['groups'][0]['video_ids'], [ID])
        self.assertEqual(result['groups'][0]['title'], '研究者')
        self.assertEqual(db.library({'q': 'no match'})['groups'][0]['video_ids'], [])

    def test_stop_feed_preserves_summaries_and_cancels_scans(self):
        self.save(source='https://www.youtube.com/@example/videos', feed=True)
        source = feeds.sources()[0]
        feeds.schedule()
        status, _ = self.call('POST', '/api/feeds', {'id': source['id'], 'enabled': False})
        self.assertEqual(status, '200 OK')
        self.assertFalse(db.library({})['groups'][0]['enabled'])
        self.assertIsNotNone(db.detail(ID)['summary'])
        self.assertIsNone(worker.claim())
        feeds.schedule()
        self.assertIsNone(worker.claim())

    def test_delete_summary_retains_transcript_and_seen_history(self):
        self.save(source='https://www.youtube.com/@example/videos', feed=True)
        worker.enqueue('process', {'force': True}, ID)
        status, _ = self.call('POST', '/api/library/delete-summary', {'id': ID})
        self.assertEqual(status, '200 OK')
        video = db.detail(ID)
        self.assertIsNone(video['summary'])
        self.assertIsNotNone(video['transcript'])
        self.assertEqual(video['mentions'], [])
        self.assertFalse(video['saved'])
        self.assertEqual(db.library({})['videos'], [])
        self.assertIsNone(worker.claim())
        with db.connect() as con:
            self.assertEqual(con.execute('SELECT count(*) FROM source_videos').fetchone()[0], 1)
        self.assertEqual(self.call('POST', '/api/library/delete-summary', {'id': ID})[0], '404 Not Found')

    def test_running_summary_cannot_be_deleted(self):
        self.save()
        worker.enqueue('process', {'force': True}, ID)
        worker.claim()
        self.assertEqual(self.call('POST', '/api/library/delete-summary', {'id': ID})[0], '400 Bad Request')
        self.assertIsNotNone(db.detail(ID)['summary'])


class VideoDedupTests(DatabaseCase):
    save = LibraryManagementTests.save

    def test_single_and_feed_share_one_record_in_either_order(self):
        feed = 'https://www.youtube.com/@example/videos'
        for first_feed in (False, True):
            with self.subTest(first_feed=first_feed):
                self.save(source=feed if first_feed else URL, feed=first_feed)
                db.add_to_library([ID], URL if first_feed else feed, not first_feed)
                result = db.library({})
                self.assertEqual(result['stats']['videos'], 1)
                self.assertEqual(result['stats']['summaries'], 1)
                self.assertEqual(result['groups'][0]['video_ids'], [ID])
                self.assertEqual(set(db.detail(ID)['source_urls']), {URL, feed})
                for link in (URL + '&t=60', f'https://youtu.be/{ID}?si=abc', f'https://www.youtube.com/shorts/{ID}'):
                    self.assertEqual(media.canonical_url(link), (URL, False))

    def test_completed_summary_is_reused_and_regeneration_is_explicit(self):
        self.save()
        first = worker.enqueue('process', {}, ID)
        self.assertEqual(worker.enqueue('process', {}, ID), first)
        self.assertIsNone(worker.claim())
        with patch('app.summarize.summarize') as inference:
            result = worker.process({'id': first, 'kind': 'process', 'video_id': ID, 'payload_json': '{}'})
            self.assertTrue(result['reused'])
            inference.assert_not_called()
        fresh = worker.enqueue('process', {'force': True}, ID)
        self.assertNotEqual(fresh, first)
        self.assertEqual(worker.enqueue('process', {}, ID), fresh)
        self.assertEqual(worker.claim()['id'], fresh)

    def test_deleted_summary_is_not_reused(self):
        self.save()
        old = worker.enqueue('process', {}, ID)
        db.delete_summary(ID)
        new = worker.enqueue('process', {}, ID)
        self.assertNotEqual(new, old)
        self.assertEqual(worker.claim()['id'], new)


class QueueVisibilityTests(DatabaseCase):
    def test_all_active_jobs_survive_history_limit_and_have_lane_positions(self):
        with db.connect() as con:
            for i in range(125):
                con.execute('INSERT INTO jobs(id,kind,payload_json,status,created_at,updated_at) VALUES(?,?,?,?,?,?)',
                            (f'active{i:03}', 'process', '{}', 'queued', '2026-01-01', '2026-01-01'))
            for i in range(110):
                con.execute('INSERT INTO jobs(id,kind,payload_json,status,created_at,updated_at) VALUES(?,?,?,?,?,?)',
                            (f'old{i:03}', 'process', '{}', 'done', '2026-02-01', '2026-02-01'))
        lookup = worker.enqueue('discover', {'url': URL, 'limit': 1})
        jobs = server.worker_jobs()
        processing = [j for j in jobs if j['kind'] == 'process' and j['status'] == 'queued']
        self.assertEqual(len(processing), 125)
        self.assertEqual([j['queue_position'] for j in processing], list(range(1, 126)))
        self.assertEqual(next(j for j in jobs if j['id'] == lookup)['queue_position'], 1)
        self.assertEqual(len([j for j in jobs if j['status'] == 'done']), 100)
        self.assertEqual(worker.claim()['id'], processing[0]['id'])

    def test_titles_resolve_before_video_is_processed_without_exposing_payload(self):
        lookup = worker.enqueue('discover', {'url': URL, 'limit': 1})
        with db.connect() as con:
            con.execute("UPDATE jobs SET status='done',result_json=? WHERE id=?", (
                db.dumps({'videos': [{'id': ID, 'title': '中文标题', 'creator': '作者'}]}), lookup))
        processing = worker.enqueue('process', {}, ID)
        job = next(j for j in server.worker_jobs() if j['id'] == processing)
        self.assertEqual(job['title'], '中文标题')
        self.assertEqual(job['creator'], '作者')
        self.assertNotIn('payload_json', job)
        self.video()
        job = next(j for j in server.worker_jobs() if j['id'] == processing)
        self.assertEqual(job['title'], '半导体研究')


class RepeatedSummarizeTests(DatabaseCase):
    def test_concurrent_requests_create_only_one_job(self):
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=8) as pool:
            ids = list(pool.map(lambda _: worker.enqueue('process', {}, ID), range(16)))
        self.assertEqual(len(set(ids)), 1)
        self.assertEqual(len(db.jobs()), 1)
        worker.claim()
        self.assertEqual(worker.enqueue('process', {}, ID), ids[0])

    def test_api_reuses_present_summary_without_queuing_processing(self):
        self.video()
        db.save_summary(ID, {'summary': '现有总结', 'category': 'other', 'mentions': []}, 'test')
        for _ in range(3):
            status, _ = self.call('POST', '/api/process', {'ids': [ID]})
            self.assertEqual(status, '200 OK')
        self.assertIsNone(worker.claim())
        self.assertEqual(len(db.jobs()), 1)
        self.assertEqual(db.detail(ID)['summary']['markdown'], '现有总结')


class RepeatedLibrarySaveTests(DatabaseCase):
    def test_concurrent_saves_add_one_item_and_source_association(self):
        from concurrent.futures import ThreadPoolExecutor
        self.video()
        db.save_summary(ID, {'summary': '总结', 'category': 'other', 'mentions': []}, 'test')
        feed = 'https://www.youtube.com/@example/videos'
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: db.add_to_library([ID], feed, True), range(16)))
        self.assertEqual(sum(r['saved'] for r in results), 1)
        self.assertEqual(sum(r['linked'] for r in results), 1)
        self.assertEqual(len(feeds.sources()), 1)
        self.assertEqual(db.saved_sources([ID]), {ID: [feed]})
        result = db.add_to_library([ID], URL, False)
        self.assertEqual(result['saved'], 0)
        self.assertEqual(result['linked'], 1)
        self.assertEqual(db.library({})['stats']['summaries'], 1)
        source = feeds.sources()[0]
        feeds.update_source(source['id'], False)
        duplicate = db.add_to_library([ID], feed, True)
        self.assertEqual(duplicate['linked'], 0)
        self.assertTrue(duplicate['monitoring'])


class AudioPreferenceRegressionTests(unittest.TestCase):
    def audio(self, ident='zh', language='zh-Hans', preference=-1, **extra):
        return dict(format_id=ident, language=language, language_preference=preference,
                    acodec='opus', vcodec='none', abr=100, **extra)

    def test_unlabelled_single_language_is_not_a_dub(self):
        self.assertEqual(media.audio_format({'formats': [self.audio()]}), 'zh')
        self.assertEqual(media.audio_format({'formats': [self.audio(language=None)]}), 'zh')

    def test_original_caption_language_matches_unknown_preference(self):
        info = {'automatic_captions': {'zh-Hans-orig': []}, 'formats': [
            self.audio(), self.audio('english', 'en', 5)]}
        self.assertEqual(media.audio_format(info), 'zh')

    def test_missing_original_never_falls_back_to_known_other_language(self):
        with self.assertRaisesRegex(RuntimeError, 'Expected language: zh'):
            media.audio_format({'automatic_captions': {'zh-orig': []}, 'formats': [self.audio('en', 'en', -1)]})

    def test_explicit_dub_and_descriptive_audio_still_rejected(self):
        for audio in [self.audio(preference=-10), self.audio(format_note='Chinese (auto-dubbed)'), self.audio(language='zh-desc')]:
            with self.subTest(audio=audio), self.assertRaises(RuntimeError):
                media.audio_format({'language': 'zh', 'formats': [audio]})

    def test_multilingual_unknown_preference_still_rejected(self):
        with self.assertRaises(RuntimeError):
            media.audio_format({'formats': [self.audio(), self.audio('en', 'en', -1)]})

    def test_empty_audio_has_actionable_error(self):
        with self.assertRaisesRegex(RuntimeError, 'no usable audio'):
            media.audio_format({'formats': [{'acodec': 'none'}]})


class WorkerDiagnosticsTests(DatabaseCase):
    def test_unexpected_failure_logs_traceback_and_safe_job_reference(self):
        ident = worker.enqueue('process', {}, ID)
        def fail(job):
            worker.STOP.set()
            raise TypeError('example dependency mismatch')
        try:
            worker.STOP.clear()
            with patch('app.worker.process', side_effect=fail), patch('app.feeds.schedule'), self.assertLogs('app.worker', level='ERROR') as logs:
                worker.run()
            self.assertIn('Traceback (most recent call last)', logs.output[0])
            self.assertIn('TypeError: example dependency mismatch', logs.output[0])
            job = db.jobs()[0]
            self.assertEqual(job['status'], 'failed')
            self.assertIn(ident, job['error'])
            self.assertIn('TypeError', job['error'])
            self.assertNotIn('example dependency mismatch', job['error'])
        finally:
            worker.STOP.clear()


class FFmpegDecodingTests(unittest.TestCase):
    def test_decodes_mono_float_samples_at_whisper_sample_rate(self):
        import struct
        import numpy as np
        from types import SimpleNamespace
        with patch('app.media.subprocess.run', return_value=SimpleNamespace(returncode=0, stdout=struct.pack('<fff', -0.5, 0, 0.5))) as run:
            samples = media.decode_audio_ffmpeg('/tmp/test.webm')
        self.assertEqual(samples.dtype, np.float32)
        self.assertEqual(samples.tolist(), [-0.5, 0, 0.5])
        command = run.call_args.args[0]
        self.assertEqual(command[command.index('-ac') + 1], '1')
        self.assertEqual(command[command.index('-ar') + 1], '16000')

    def test_missing_ffmpeg_and_invalid_audio_are_actionable(self):
        from types import SimpleNamespace
        with patch('app.media.subprocess.run', side_effect=FileNotFoundError), self.assertRaisesRegex(RuntimeError, 'Install ffmpeg'):
            media.decode_audio_ffmpeg('test.webm')
        for code, data in [(1, b''), (0, b''), (0, b'bad')]:
            with patch('app.media.subprocess.run', return_value=SimpleNamespace(returncode=code, stdout=data)), self.assertRaises(RuntimeError):
                media.decode_audio_ffmpeg('test.webm')

    def test_transcription_passes_samples_not_filename_to_whisper(self):
        import sys
        import numpy as np
        from types import SimpleNamespace
        from unittest.mock import Mock
        samples = np.zeros(16000, dtype=np.float32)
        model = Mock()
        model.transcribe.return_value = (iter([SimpleNamespace(start=0, end=1, text='中文')]), SimpleNamespace(language='zh'))
        def download(args):
            Path(args[args.index('-o') + 1].replace('%(ext)s', 'webm')).write_bytes(b'audio')
        with tempfile.TemporaryDirectory() as tmp, patch.object(config, 'DATA', Path(tmp)), patch.object(media, '_whisper', model), patch.dict(sys.modules, {'faster_whisper': SimpleNamespace(WhisperModel=Mock())}), patch('app.media.run_ytdlp', side_effect=download), patch('app.media.decode_audio_ffmpeg', return_value=samples):
            result = media.transcribe({'id': ID, 'formats': [{'format_id': '140', 'acodec': 'aac'}]})
        self.assertIs(model.transcribe.call_args.args[0], samples)
        self.assertEqual(model.transcribe.call_args.kwargs['task'], 'transcribe')
        self.assertIsNone(model.transcribe.call_args.kwargs['language'])
        self.assertEqual(result[0], 'zh')


class InferenceErrorTests(unittest.TestCase):
    def test_http_and_timeout_failures_are_distinct_from_connection_errors(self):
        from urllib.error import HTTPError, URLError
        for error, expected in [
            (HTTPError('http://localhost', 503, 'Unavailable', {}, None), 'HTTP 503'),
            (HTTPError('http://localhost', 400, 'Bad request', {}, None), 'HTTP 400'),
            (TimeoutError(), 'timed out after 900'),
            (URLError(TimeoutError()), 'connection timed out'),
            (URLError(ConnectionRefusedError()), 'connection failed'),
        ]:
            with self.subTest(error=error), patch('app.summarize.urlopen', side_effect=error), self.assertRaisesRegex(RuntimeError, expected):
                summarize.chat('system', 'text')


class InferenceResponseDiagnosticsTests(unittest.TestCase):
    def test_http_error_logs_server_message_without_request_text(self):
        from urllib.error import HTTPError
        error = HTTPError('http://localhost', 400, 'Bad request', {}, io.BytesIO(json.dumps({
            'error': {'message': 'request exceeds available context', 'type': 'invalid_request_error'},
            'unrelated': 'do not log full response',
        }).encode()))
        with patch('app.summarize.urlopen', side_effect=error), self.assertLogs('app.summarize', level='ERROR') as logs:
            with self.assertRaisesRegex(RuntimeError, 'HTTP 400'):
                summarize.chat('system', 'private transcript')
        self.assertIn('request exceeds available context', logs.output[0])
        self.assertNotIn('private transcript', logs.output[0])
        self.assertNotIn('do not log full response', logs.output[0])


class SerializedChunkTests(unittest.TestCase):
    def test_many_short_chinese_segments_include_json_overhead_in_budget(self):
        segments = [{'start': i * 0.5, 'end': i * 0.5 + 0.5, 'text': '中文短句'} for i in range(600)]
        batches = list(summarize.chunks(segments))
        self.assertGreater(len(batches), 1)
        self.assertTrue(all(len(summarize.serialize_segments(b).encode('utf-8')) <= 2400 for b in batches))
        self.assertEqual([s for b in batches for s in b], segments)

    def test_long_segment_unicode_and_json_escapes_preserved(self):
        text = '中文😀"\\\n' * 1200
        batches = list(summarize.chunks([{'start': 42, 'text': text}]))
        self.assertEqual(''.join(s['text'] for b in batches for s in b), text)
        self.assertTrue(all(len(summarize.serialize_segments(b).encode('utf-8')) <= 2400 for b in batches))
        self.assertTrue(all(s['start'] == 42 for b in batches for s in b))


class RetryQueueTests(DatabaseCase):
    def failed(self):
        ident = worker.enqueue('process', {}, ID)
        with db.connect() as con:
            con.execute("UPDATE jobs SET status='failed',error='old error',result_json='{}' WHERE id=?", (ident,))
        return ident

    def test_repeated_retries_reuse_same_row_and_clear_old_error(self):
        ident = self.failed()
        for _ in range(3):
            status, body = self.call('POST', '/api/retry', {'id': ident})
            self.assertEqual(status, '200 OK')
            self.assertEqual(json.loads(body)['job_id'], ident)
        job = worker.claim()
        self.assertEqual(job['id'], ident)
        self.assertIsNone(job['error'])
        self.assertIsNone(job['result_json'])
        self.assertEqual(worker.retry(ident), ident)
        with db.connect() as con:
            self.assertEqual(con.execute('SELECT count(*) FROM jobs').fetchone()[0], 1)

    def test_historical_attempts_show_only_active_or_latest_for_video(self):
        old = self.failed()
        latest = self.failed()
        self.assertEqual(len(db.jobs()), 1)
        self.assertEqual(db.jobs()[0]['id'], latest)
        worker.retry(old)
        self.assertEqual(worker.retry(latest), old)
        self.assertEqual([j['id'] for j in db.jobs()], [old])
        with db.connect() as con:
            self.assertEqual(con.execute('SELECT count(*) FROM jobs').fetchone()[0], 2)

    def test_concurrent_retry_clicks_share_one_active_job(self):
        from concurrent.futures import ThreadPoolExecutor
        ident = self.failed()
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: worker.retry(ident), range(16)))
        self.assertEqual(set(results), {ident})
        self.assertEqual(worker.claim()['id'], ident)
        self.assertIsNone(worker.claim())


class MergeValidationTests(unittest.TestCase):
    def test_empty_merge_retries_only_merge(self):
        responses = [
            {'summary': '第一段摘要', 'category': 'finance'},
            {'summary': '第二段摘要', 'category': 'finance'},
            {}, {'summary': '合并后的中文摘要'},
        ]
        stages = []
        with patch('app.summarize.chat', side_effect=responses) as chat:
            result = summarize.summarize('zh', [{'start': 0, 'text': '中' * 1200}], stages.append)
        self.assertEqual(result['summary'], '合并后的中文摘要')
        self.assertEqual(chat.call_count, 4)
        self.assertEqual(chat.call_args_list[2].args[1], chat.call_args_list[3].args[1])
        self.assertTrue(any('Retrying' in stage for stage in stages))

    def test_invalid_output_retry_is_bounded(self):
        with patch('app.summarize.chat', return_value={'summary': '   '}) as chat:
            with self.assertRaises(summarize.ModelOutputError):
                summarize.validated_chat('system', 'text', 'zh', lambda _: None)
        self.assertEqual(chat.call_count, 2)

    def test_connection_failure_does_not_trigger_output_retry(self):
        with patch('app.summarize.chat', side_effect=RuntimeError('connection failed')) as chat:
            with self.assertRaisesRegex(RuntimeError, 'connection failed'):
                summarize.validated_chat('system', 'text', 'zh', lambda _: None)
        self.assertEqual(chat.call_count, 1)


class LibraryAutoSummaryTests(DatabaseCase):
    def test_repeated_and_cross_source_saves_share_processing_job(self):
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: db.add_to_library([ID], URL, False), range(16)))
        ident = results[0]['job_ids'][0]
        self.assertTrue(all(r['job_ids'] == [ident] for r in results))
        self.assertEqual(worker.claim()['id'], ident)
        self.assertEqual(db.add_to_library([ID], 'https://www.youtube.com/@example/videos', True)['job_ids'], [ident])
        self.assertIsNone(worker.claim())
        with db.connect() as con:
            con.execute("UPDATE jobs SET status='failed',error='test' WHERE id=?", (ident,))
        self.assertEqual(db.add_to_library([ID], URL, False)['job_ids'], [ident])
        self.assertEqual(worker.claim()['id'], ident)
        with db.connect() as con:
            con.execute("UPDATE jobs SET status='done' WHERE id=?", (ident,))
        db.save_summary(ID, {'summary': '中文总结', 'category': 'other', 'mentions': []}, 'test')
        self.assertEqual(db.add_to_library([ID], URL, False)['job_ids'], [])
        self.assertIsNone(worker.claim())


class ChannelFeedTests(DatabaseCase):
    channel = 'UC' + 'a' * 22

    def monitored(self):
        ident = feeds.subscribe('https://www.youtube.com/channel/' + self.channel + '/videos', '2026-01-01T00:00:00+00:00')
        with db.connect() as con:
            con.execute('UPDATE sources SET channel_id=?,last_reconciled=?,last_checked=?,checkpoint=? WHERE id=?',
                        (self.channel, db.now(), db.now(), '2026-01-01T00:00:00+00:00', ident))
        return ident

    def test_feed_parser_preserves_native_title_and_date(self):
        from app import channel_feed
        xml = f'''<feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015"><yt:channelId>{self.channel}</yt:channelId><entry><yt:videoId>{ID}</yt:videoId><title>中文标题</title><published>2026-02-01T12:00:00Z</published><author><name>作者</name></author></entry></feed>'''
        with patch('app.channel_feed.urlopen', return_value=io.BytesIO(xml.encode())):
            videos = channel_feed.read(self.channel)
        self.assertEqual(videos[0]['title'], '中文标题')
        self.assertEqual(videos[0]['published_at'], '2026-02-01T12:00:00+00:00')

    def test_feed_poll_deduplicates_and_does_not_advance_backfill_checkpoint(self):
        ident = self.monitored()
        video = self.video(published='2026-02-01T00:00:00+00:00')
        with patch('app.channel_feed.read', return_value=[video]), patch('app.media.discover') as discover:
            self.assertEqual(feeds.sync(ident, lambda _: None)['method'], 'feed')
            feeds.sync(ident, lambda _: None)
            discover.assert_not_called()
        self.assertEqual(len([j for j in db.jobs() if j['kind'] == 'process']), 1)
        self.assertEqual(feeds.sources()[0]['checkpoint'], '2026-01-01T00:00:00+00:00')
        self.assertEqual(db.library({})['stats']['videos'], 1)

    def test_feed_failure_falls_back_to_listing(self):
        ident = self.monitored()
        with patch('app.channel_feed.read', side_effect=ValueError('unavailable')), patch('app.media.discover', return_value={'videos': [], 'warnings': [], 'possibly_more': False}) as discover:
            result = feeds.sync(ident, lambda _: None)
        discover.assert_called_once()
        self.assertTrue(result['backfill_complete'])

    def test_downtime_or_periodic_reconciliation_uses_listing(self):
        for column in ('last_checked', 'last_reconciled'):
            ident = self.monitored()
            with db.connect() as con:
                con.execute(f'UPDATE sources SET {column}=? WHERE id=?', ('2026-01-01T00:00:00+00:00', ident))
            with patch('app.channel_feed.read') as feed, patch('app.media.discover', return_value={'videos': [], 'warnings': [], 'possibly_more': False}) as discover:
                feeds.sync(ident, lambda _: None)
            feed.assert_not_called()
            discover.assert_called_once()


class DubbedCaptionRegressionTests(unittest.TestCase):
    def test_english_metadata_overrides_first_arabic_caption_and_matches_region(self):
        arabic = {'ext': 'json3', 'url': 'https://www.youtube.com/api/timedtext?lang=ar'}
        english = {'ext': 'json3', 'url': 'https://www.youtube.com/api/timedtext?lang=en'}
        info = {'language': 'en-US', 'automatic_captions': {'ar-orig': [arabic], 'en-orig': [english]}}
        self.assertEqual(media.original_language(info), 'en-US')
        self.assertEqual(media.native_track(info)[1], english['url'])
        info.pop('language')
        self.assertIsNone(media.native_track(info))

    def test_missing_english_captions_never_selects_arabic(self):
        self.assertIsNone(media.native_track({'language': 'en-US', 'automatic_captions': {'ar-orig': [
            {'ext': 'json3', 'url': 'https://www.youtube.com/api/timedtext?lang=ar'}]}}))


class TranscriptLanguageRepairTests(DatabaseCase):
    def test_retry_replaces_mismatched_transcript_before_summarizing(self):
        video = self.video()
        video['metadata'] = {'language': 'en-US'}
        db.save_video(video)
        db.save_transcript(ID, 'ar', 'youtube_original_captions', [{'start': 0, 'text': 'نص'}])
        with patch('app.media.video_info', return_value={'id': ID, 'title': 'English video', 'language': 'en-US'}), patch('app.media.transcribe', return_value=('en-US', 'youtube_original_captions', [{'start': 0, 'text': 'Original English speech'}])) as transcribe, patch('app.summarize.summarize', return_value={'summary': 'English summary', 'category': 'other', 'mentions': []}) as summary:
            worker.process({'id': 'test', 'kind': 'process', 'video_id': ID, 'payload_json': '{"force":true}'})
        transcribe.assert_called_once()
        self.assertEqual(summary.call_args.args[0], 'en-US')
        self.assertEqual(db.detail(ID)['transcript']['language'], 'en-US')


class OutputLanguagePromptTests(unittest.TestCase):
    def test_english_retry_explicitly_requires_english(self):
        with patch('app.summarize.chat', side_effect=[{'summary': '这是错误的中文总结'}, {'summary': 'Semiconductor stocks remain cyclical.'}]) as chat:
            result = summarize.validated_chat(summarize.SYSTEM, 'English source', 'en-US', lambda _: None)
        self.assertEqual(result['summary'], 'Semiconductor stocks remain cyclical.')
        for call in chat.call_args_list:
            self.assertIn('OUTPUT LANGUAGE: ENGLISH', call.args[0])
            self.assertNotIn('Preserve simplified/traditional Chinese', call.args[0])
        self.assertIn('previous response failed validation', chat.call_args.args[0])

    def test_arabic_summary_cannot_pass_english_validation(self):
        with self.assertRaises(summarize.ModelOutputError):
            summarize.checked_summary({'summary': 'في هذا التقرير استعرضنا آليات السوق NVDA'}, 'en-US')

    def test_chinese_request_has_chinese_specific_instruction(self):
        with patch('app.summarize.chat', return_value={'summary': '半导体行业具有周期性。'}) as chat:
            summarize.validated_chat(summarize.SYSTEM, '中文原文', 'zh-Hans', lambda _: None)
        self.assertIn('OUTPUT LANGUAGE: CHINESE', chat.call_args.args[0])


class IndexedSearchTests(DatabaseCase):
    def test_indexes_track_saved_content_updates_and_removal(self):
        self.video()
        db.save_transcript(ID, 'zh', 'test', [{'start': 0, 'text': '估值 NVIDIA 100% "quoted" a_b'}])
        self.assertEqual(db.library({'q': 'NVIDIA'})['videos'], [])
        db.add_to_library([ID], URL, False)
        for term in ('估值', '值', 'nvidia', '100%', '"quoted"', 'a_b', '%', '_'):
            self.assertEqual(len(db.library({'q': term})['videos']), 1, term)
        db.save_transcript(ID, 'zh', 'test', [{'start': 0, 'text': '新的原文'}])
        self.assertEqual(db.library({'q': 'NVIDIA'})['videos'], [])
        self.assertEqual(db.library({'q': '估值'})['videos'], [])
        db.save_summary(ID, {'summary': '投资风险 longterm', 'category': 'finance', 'mentions': []}, 'test')
        self.assertEqual(len(db.library({'q': '投资风险'})['videos']), 1)
        db.delete_summary(ID)
        self.assertEqual(db.library({'q': '投资'})['videos'], [])
        with db.connect() as con:
            self.assertEqual(con.execute('SELECT count(*) FROM video_search').fetchone()[0], 0)
            self.assertEqual(con.execute('SELECT count(*) FROM search_short_terms').fetchone()[0], 0)

    def test_migration_backfills_existing_saved_content_once(self):
        self.video()
        db.add_to_library([ID], URL, False)
        with db.connect() as con:
            con.execute('DROP TABLE video_search')
            con.execute('DROP TABLE search_short_terms')
            con.execute('DELETE FROM schema_version WHERE version=5')
        db.init()
        db.init()
        self.assertEqual(len(db.library({'q': '半导体'})['videos']), 1)
        self.assertEqual(len(db.library({'q': '研究'})['videos']), 1)

    def test_query_plans_use_search_indexes_and_primary_key_candidates(self):
        from contextlib import contextmanager
        original = db.connect
        statements = []
        @contextmanager
        def traced():
            with original() as con:
                con.set_trace_callback(statements.append)
                yield con
        for term, index in [('semiconductor', 'VIRTUAL TABLE INDEX'), ('估值', 'PRIMARY KEY (term=?)')]:
            statements.clear()
            with patch('app.db.connect', traced):
                db.library({'q': term})
            query = next(s for s in statements if s.startswith('SELECT v.id FROM videos v'))
            with original() as con:
                plan = '\n'.join(r[3] for r in con.execute('EXPLAIN QUERY PLAN ' + query))
            self.assertIn(index, plan)
            self.assertNotIn('SCAN v', plan.replace('SCAN video_search', 'FTS'))


class SearchBackendBoundaryTests(DatabaseCase):
    def test_library_uses_backend_filters_and_preserves_result_order(self):
        from app import search
        from unittest.mock import Mock
        other = '12345678901'
        self.video()
        self.video(other)
        db.add_to_library([ID], URL, False)
        db.add_to_library([other], 'https://www.youtube.com/watch?v=' + other, False)
        backend = Mock()
        backend.find.return_value = [other, 'missing0000', ID]
        filters = {'q': 'custom search', 'ticker': 'NVDA'}
        with patch.object(search, 'backend', backend):
            result = db.library(filters)
        self.assertEqual([v['id'] for v in result['videos']], [other, ID])
        self.assertEqual(backend.find.call_args.args[1:], (filters, 1000))

    def test_mutations_notify_backend_in_transaction(self):
        from app import search
        from unittest.mock import Mock
        self.video()
        backend = Mock()
        with patch.object(search, 'backend', backend):
            db.save_transcript(ID, 'en', 'test', [{'start': 0, 'text': 'New transcript'}])
        self.assertEqual(backend.sync.call_args.args[1], ID)


class LLMSettingsTests(DatabaseCase):
    def test_key_is_persisted_private_retained_or_explicitly_cleared(self):
        from app import llm_settings
        settings = {'endpoint': 'https://example.com/v1', 'model': 'small-model', 'format': 'json', 'api_key': 'secret-key'}
        status, body = self.call('POST', '/api/llm-settings', settings)
        self.assertEqual(status, '200 OK')
        self.assertNotIn(b'secret-key', body)
        self.assertTrue(json.loads(body)['key_configured'])
        self.assertEqual((config.DATA/'llm-settings.json').stat().st_mode & 0o777, 0o600)
        self.call('POST', '/api/llm-settings', {'api_key': ''})
        self.assertEqual(llm_settings.load()['api_key'], 'secret-key')
        self.assertNotIn(b'secret-key', self.call('GET', '/api/llm-settings')[1])
        self.call('POST', '/api/llm-settings', {'clear_key': True})
        self.assertEqual(llm_settings.load()['api_key'], '')

    def test_invalid_endpoints_do_not_change_settings(self):
        for endpoint in ('file:///tmp/model', 'https://user:pass@example.com', 'http://localhost:bad', 'https://example.com?api_key=secret'):
            self.assertEqual(self.call('POST', '/api/llm-settings', {'endpoint': endpoint})[0], '400 Bad Request')
        self.assertFalse((config.DATA/'llm-settings.json').exists())

    def test_remote_requests_use_model_key_and_compatible_format(self):
        from app import llm_settings
        llm_settings.save({'endpoint': 'https://example.com/v1', 'model': 'remote-model', 'api_key': 'secret', 'format': 'json'})
        response = io.BytesIO(b'{"choices":[{"message":{"content":"{\\"summary\\":\\"English summary\\"}"},"finish_reason":"stop"}]}')
        with patch('app.summarize.urlopen', return_value=response) as call:
            summarize.chat('system', 'text')
        req = call.call_args.args[0]
        self.assertEqual(req.full_url, 'https://example.com/v1/chat/completions')
        self.assertEqual(req.get_header('Authorization'), 'Bearer secret')
        self.assertEqual(json.loads(req.data)['model'], 'remote-model')
        self.assertEqual(json.loads(req.data)['response_format'], {'type': 'json_object'})

    def test_running_summary_configuration_does_not_change(self):
        from app import llm_settings
        initial = llm_settings.load()
        with llm_settings.use(initial):
            llm_settings.save({'model': 'replacement'})
            self.assertEqual(llm_settings.current()['model'], initial['model'])
        self.assertEqual(llm_settings.current()['model'], 'replacement')

    def test_connection_test_does_not_save_settings(self):
        with patch('app.server.urlopen', return_value=io.BytesIO(b'{"data":[{"id":"remote"}]}')) as call:
            status, body = self.call('POST', '/api/llm-settings/test', {'endpoint': 'https://example.com/v1', 'model': 'remote', 'api_key': 'secret'})
        self.assertEqual(status, '200 OK')
        self.assertTrue(json.loads(body)['model_ready'])
        self.assertEqual(call.call_args.args[0].get_header('Authorization'), 'Bearer secret')
        self.assertFalse((config.DATA/'llm-settings.json').exists())
