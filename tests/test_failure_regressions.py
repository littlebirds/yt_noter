import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app import slides, summarize


class SlideDecodeRegressionTests(unittest.TestCase):
    def test_valid_image_is_not_autoprobed_as_transport_stream(self):
        tsv = b'level\tleft\ttop\twidth\theight\tconf\ttext\n1\t0\t0\t640\t360\t-1\t\n'
        def run(command, **kwargs):
            if command[0] == 'tesseract':
                return SimpleNamespace(returncode=0,stdout=tsv)
            if command[command.index('-i')-2:command.index('-i')] == ['-f','image2pipe']:
                return SimpleNamespace(returncode=0,stdout=b'\xff'*(640*360))
            return SimpleNamespace(returncode=187,stdout=b'',stderr=b'[in#0/mpegts] could not find codec parameters')
        with patch('app.slides.subprocess.run',side_effect=run),patch('app.slide_content.classify',return_value={'kind':'chart','region':[20,20,500,300]}) as classify:
            self.assertEqual(slides.educational_image(b'jpeg','eng')['kind'],'chart')
        classify.assert_called_once()

    def test_bad_or_short_decode_still_fails_instead_of_becoming_empty_scan(self):
        tsv = b'level\tleft\ttop\twidth\theight\tconf\ttext\n1\t0\t0\t640\t360\t-1\t\n'
        with patch('app.slides.subprocess.run',side_effect=[SimpleNamespace(returncode=0,stdout=tsv),SimpleNamespace(returncode=0,stdout=b'bad')]):
            with self.assertRaisesRegex(RuntimeError,'Could not analyze'):
                slides.educational_image(b'bad','eng')


class SummaryCapacityRegressionTests(unittest.TestCase):
    def test_notes_above_style_target_but_within_capacity_are_accepted(self):
        note = '中文'*250  # 1500 bytes, above the 1200-byte writing target.
        section = {'summary':note,'category':'finance','mentions':[]}
        source = [{'start':0,'text':'中'*1000},{'start':100,'text':'中'*1000}]
        with patch('app.summarize.chat',side_effect=[section.copy(),section.copy(),{'summary':'最终中文摘要'}]) as chat:
            result = summarize.summarize('zh',source)
        self.assertEqual(result['summary'],'最终中文摘要')
        self.assertEqual(chat.call_count,3)
        self.assertIn(note,chat.call_args_list[-1].args[1])

    def test_oversized_note_retry_gives_measured_feedback(self):
        with summarize.request_options(1400,2000),patch('app.summarize.chat',side_effect=[{'summary':'中'*1000},{'summary':'精简后的中文摘要'}]) as chat:
            result=summarize.validated_chat('system','source','zh',lambda _:None)
        self.assertEqual(result['summary'],'精简后的中文摘要')
        self.assertIn('3000 UTF-8 bytes',chat.call_args_list[1].args[0])
        self.assertIn('safe limit is 2000',chat.call_args_list[1].args[0])

    def test_unbounded_notes_still_fail_after_one_retry(self):
        with summarize.request_options(1400,2000),patch('app.summarize.chat',return_value={'summary':'中'*1000}) as chat:
            with self.assertRaises(summarize.OversizedNoteError):
                summarize.validated_chat('system','source','zh',lambda _:None)
        self.assertEqual(chat.call_count,2)
