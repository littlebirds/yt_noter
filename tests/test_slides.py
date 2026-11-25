import json
import unittest
from unittest.mock import patch

import numpy as np

from app import db, slides, worker, server
from test_app import DatabaseCase, ID


def chart():
    frame = np.full((180, 320), 240, dtype=np.uint8)
    frame[20:160:12, 20:300] = 20
    frame[20:160, 20:300:40] = 20
    return frame


class DetectionTests(unittest.TestCase):
    def test_requires_four_seconds_and_flushes_final_slide(self):
        frame = chart()
        self.assertEqual(list(slides.candidates(( (i / 2, frame) for i in range(8)))), [])
        result = list(slides.candidates(( (i / 2, frame) for i in range(9))))
        self.assertEqual(result, [{'start': 0, 'end': 4, 'timestamp': 0}])

    def test_animation_and_blank_are_excluded(self):
        self.assertEqual(list(slides.candidates((i / 2, np.roll(chart(), i * 3, axis=0)) for i in range(20))), [])
        self.assertEqual(list(slides.candidates((i / 2, np.zeros((180, 320), dtype=np.uint8)) for i in range(20))), [])

    def test_changes_split_holds_and_preserve_timestamps(self):
        frames = [(i / 2, chart() if i < 10 else 255-chart()) for i in range(20)]
        result = list(slides.candidates(frames))
        self.assertEqual([s['timestamp'] for s in result], [0, 5])
        self.assertEqual([s['end'] for s in result], [4.5, 9.5])

    def test_changing_subtitles_do_not_split_stable_chart(self):
        frames = []
        for i in range(12):
            frame = chart()
            frame[:20] = (i % 2) * 255
            frame[-20:] = (i % 3) * 100
            frames.append((i / 2, frame))
        self.assertFalse(slides.similar(frames[0][1], frames[1][1]))
        result = list(slides.candidates(frames))
        self.assertEqual(result, [{'start': 0, 'end': 5.5, 'timestamp': 0}])

    def test_subtitles_alone_do_not_make_a_slide(self):
        frames = []
        for i in range(12):
            frame = np.full((180, 320), 240, dtype=np.uint8)
            frame[:20, ::2] = (i % 2) * 255
            frames.append((i / 2, frame))
        self.assertEqual(list(slides.candidates(frames)), [])

    def test_slow_changes_are_compared_to_anchor(self):
        result = list(slides.candidates((i / 2, np.clip(chart().astype(int)-i*3, 0, 255).astype(np.uint8)) for i in range(20)))
        self.assertEqual(result, [])


class SlidePersistenceTests(DatabaseCase):
    def test_scan_job_recovery_and_atomic_replace(self):
        self.video()
        db.save_transcript(ID, 'zh', 'test', [{'start':0,'text':'原文'}])
        db.save_summary(ID, {'summary':'中文摘要','category':'other','mentions':[]}, 'test')
        job_id = worker.enqueue('slides', {'minimum': 4}, ID)
        self.assertEqual(worker.enqueue('slides', {'minimum': 4}, ID), job_id)
        job = worker.claim()
        db.init()
        self.assertEqual(worker.claim()['id'], job_id)
        sample = [{'start': 8, 'end': 12, 'timestamp': 8, 'image': b'jpeg', 'content_kind': 'chart'}]
        with patch('app.slides.extract', return_value=sample):
            worker.process(job)
        self.assertEqual(db.detail(ID)['slides'][0]['timestamp'], 8)
        self.assertEqual(db.detail(ID)['slides'][0]['content_kind'], 'chart')
        with patch('app.slides.extract', side_effect=RuntimeError('failed')):
            with self.assertRaises(RuntimeError):
                worker.process(job)
        self.assertEqual(len(db.detail(ID)['slides']), 1)
        with patch('app.slides.extract', return_value=[]):
            worker.process(job)
        self.assertEqual(db.detail(ID)['slides'], [])
        self.assertEqual(db.detail(ID)['slide_scan']['minimum'], 4)

    def test_summary_failure_keeps_one_composite_job(self):
        self.video()
        db.save_transcript(ID, 'zh', 'test', [{'start':0,'text':'原文'}])
        ident=worker.enqueue('process',{},ID)
        job=worker.claim()
        with patch('app.summarize.summarize',side_effect=RuntimeError('inference failed')), patch('app.slides.extract') as scan:
            with self.assertRaises(RuntimeError):
                worker.process(job)
        scan.assert_not_called()
        self.assertIsNone(worker.claim())
        self.assertEqual([j['id'] for j in db.jobs()],[ident])

    def test_api_validation_and_image_auth(self):
        self.video()
        for value in (0, 61, True, 4.5, '4'):
            status, _ = self.call('POST', '/api/slides', {'id': ID, 'minimum': value})
            self.assertTrue(status.startswith('400'))
        status, body = self.call('POST', '/api/slides', {'id': ID})
        self.assertTrue(status.startswith('200'))
        self.assertIn('job_id', json.loads(body))
        db.save_slides(ID, [{'start': 0, 'end': 4, 'timestamp': 0, 'image': b'jpeg'}], 4)
        with patch.object(server, 'TOKEN', 'secret'):
            self.assertTrue(self.call('GET', f'/api/slides/{ID}/0')[0].startswith('401'))
            status, body = self.call('GET', f'/api/slides/{ID}/0', token='Bearer secret')
            self.assertTrue(status.startswith('200'))
            self.assertTrue(json.loads(body)['image'].startswith('data:image/jpeg;base64,'))
        db.init()
        self.assertEqual(len(db.detail(ID)['slides']), 1)


class ContentFilterTests(unittest.TestCase):
    def test_discord_conversation_with_table_is_excluded(self):
        text = '@everyone trade update\n@everyone second update\nQQQ Jan 15 590 P 17.90 LIMIT'
        self.assertTrue(slides.conversation_screen(text))
        self.assertTrue(slides.conversation_screen('# channel\n' * 6 + '@alice\nRevenue 2026 12.3'))

    def test_english_and_chinese_comment_threads_are_excluded(self):
        for text in ('@alice 2 days ago Reply 12 likes\n@bob 3 hours ago Reply 5 likes',
                     '用户甲 2小时前 回复 点赞\n用户乙 3天前 回复 点赞',
                     '用戶甲 2小時前 回覆 讚\n用戶乙 3天前 回覆 讚'):
            self.assertTrue(slides.conversation_screen(text))

    def test_educational_text_is_not_rejected_for_isolated_words(self):
        for text in ('Equity Positioning\nCTA Positioning in NDX ($bn)\nHigh Low Avg Last StdDev',
                     'Learning goals\nEveryone can learn\nEveryone can practice',
                     'How to reply to comments\n1. Read carefully\n2. Explain your reasoning',
                     'Year Revenue Profit\n2024 100 20\n2025 120 25',
                     '課程重點\n回覆問題\n比較圖表'):
            self.assertFalse(slides.conversation_screen(text))

    def test_ocr_failures_do_not_silently_accept_images(self):
        import subprocess
        from types import SimpleNamespace
        with patch('app.slides.subprocess.run', return_value=SimpleNamespace(returncode=1)):
            with self.assertRaises(RuntimeError):
                slides.educational_image(b'jpeg', 'eng')
        with patch('app.slides.subprocess.run', side_effect=subprocess.TimeoutExpired('tesseract', 60)):
            with self.assertRaises(RuntimeError):
                slides.educational_image(b'jpeg', 'eng')
        with patch('app.slides.subprocess.run', side_effect=FileNotFoundError):
            with self.assertRaisesRegex(RuntimeError, 'requires Tesseract'):
                slides.ocr_languages()

    def test_extraction_filters_before_persistence(self):
        from types import SimpleNamespace
        def download(args):
            from pathlib import Path
            Path(args[args.index('-o') + 1].replace('%(ext)s', 'mp4')).write_bytes(b'video')
        candidate = {'start': 10, 'end': 15, 'timestamp': 10}
        with patch('app.slides.ocr_languages', return_value='eng'), \
             patch('app.slides.media.run_ytdlp', side_effect=download), \
             patch('app.slides.candidates', return_value=iter([candidate])), \
             patch('app.slides.subprocess.run', return_value=SimpleNamespace(returncode=0, stdout=b'jpeg')), \
             patch('app.slides.educational_image', return_value=False) as filter_image:
            self.assertEqual(slides.extract(ID), [])
            filter_image.assert_called_once_with(b'jpeg', 'eng')
