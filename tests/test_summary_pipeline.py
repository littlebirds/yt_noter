import json
import unittest
from unittest.mock import patch

from app import config, db, llm_settings, summarize, worker
from test_app import DatabaseCase, ID


def response(summary='中文要点与风险'):
    return {'summary': summary, 'category': 'finance', 'mentions': []}


class CompactEvidenceTests(unittest.TestCase):
    def test_compact_input_omits_times_but_keeps_exact_source_mapping(self):
        source = [{'start': 42.123, 'end': 48, 'text': 'NVDA 中文\n"quoted"|😀'},
                  {'start': 99.5, 'text': 'NVDA 中文\n"quoted"|😀'}]
        batch = list(summarize.chunks(source))[0]
        serialized = summarize.serialize_segments(batch)
        self.assertNotIn('42.123', serialized)
        self.assertNotIn('"start"', serialized)
        self.assertEqual(json.loads(serialized.splitlines()[0].split('|',1)[1]), source[0]['text'])
        result = {'mentions': [{'segment_id': 1, 'ticker':'NVDA', 'evidence':'NVDA 中文'}]}
        self.assertEqual(summarize.checked_mentions(result,batch)[0]['timestamp'],99.5)
        for bad_id in (2, True, '1', None):
            result['mentions'][0]['segment_id'] = bad_id
            self.assertEqual(summarize.checked_mentions(result,batch), [])

    def test_grouped_merge_keeps_tail_and_packs_more_than_pairs(self):
        notes = [f'Note {i}: '+ 'x'*100 for i in range(11)]
        groups = list(summarize.merge_groups(notes, 500))
        self.assertGreater(len(groups[0]), 2)
        self.assertEqual([n for g in groups for n in g], notes)
        self.assertTrue(all(len('\n\n'.join(g).encode()) <= 500 for g in groups))
        with self.assertRaises(summarize.ModelOutputError):
            list(summarize.merge_groups(['x'*501],500))

    def test_request_budget_reserves_output_and_prompt_for_both_languages(self):
        for language in ('en','zh-Hant'):
            system = summarize.SYSTEM + summarize.NOTE_INSTRUCTION
            budget = summarize.input_budget(system,language,summarize.SECTION_TOKENS)
            self.assertLessEqual(budget + len(summarize.system_prompt(system,language).encode())
                                 + summarize.SECTION_TOKENS + 1024,config.LLM_CONTEXT_TOKENS)
        with patch.object(config,'LLM_CONTEXT_TOKENS',1024):
            with self.assertRaises(ValueError):
                summarize.input_budget(system,'zh',1400)

    def test_short_video_gets_full_summary_without_extra_merge(self):
        output = response('中文详细摘要。'*100)
        budgets = []
        def chat(system,text):
            budgets.append(summarize._request_options.get())
            return output
        with patch('app.summarize.chat',side_effect=chat) as call:
            result=summarize.summarize('zh',[{'start':0,'text':'中文原文'}])
        self.assertEqual(call.call_count,1)
        self.assertEqual(result['summary'],output['summary'])
        self.assertEqual(budgets,[(summarize.FINAL_TOKENS,None)])

    def test_many_short_captions_need_far_fewer_calls(self):
        segments = [{'start': i*2,'end':i*2+2,'text':'这是关于市场风险和收益的分析。'} for i in range(939)]
        calls=[]
        def chat(system,text):
            calls.append(text)
            return response()
        with patch('app.summarize.chat',side_effect=chat):
            summarize.summarize('zh',segments)
        # This longer synthetic text still needs substantially fewer than 57 calls.
        self.assertLess(len(calls),20)
        self.assertTrue(any('S938|' in text for text in calls))


class CheckpointTests(DatabaseCase):
    def setUp(self):
        super().setUp()
        self.video()
        self.ident = worker.enqueue('process',{},ID)
        self.source = [{'start':i*100,'text':'中文'*400} for i in range(4)]

    def run_summary(self):
        with patch('app.summarize.input_budget',return_value=2600):
            return summarize.summarize('zh',self.source,checkpoint_job=self.ident)

    def test_resume_completed_sections_after_failure_and_restart(self):
        with patch('app.summarize.chat',side_effect=[response(),response(),RuntimeError('offline')]) as chat:
            with self.assertRaises(RuntimeError):
                self.run_summary()
        self.assertEqual(chat.call_count,3)
        db.init()
        with patch('app.summarize.chat',return_value=response()) as chat:
            result=self.run_summary()
        self.assertEqual(chat.call_count,3)  # two remaining sections and one grouped merge
        self.assertEqual(result['summary'],'中文要点与风险')
        with patch('app.summarize.chat') as chat:
            self.run_summary()
        chat.assert_not_called()

    def test_resume_failed_merge_without_repeating_sections(self):
        with patch('app.summarize.chat',side_effect=[response()]*4+[RuntimeError('offline')]):
            with self.assertRaises(RuntimeError):
                self.run_summary()
        with patch('app.summarize.chat',return_value=response()) as chat:
            self.run_summary()
        self.assertEqual(chat.call_count,1)

    def test_regeneration_new_job_and_changed_inputs_invalidate_cache(self):
        with patch('app.summarize.chat',return_value=response()) as chat:
            self.run_summary()
            self.assertEqual(chat.call_count,5)
            self.source[0]['text'] += '新'
            chat.reset_mock(); self.run_summary()
            self.assertEqual(chat.call_count,5)
            settings=llm_settings.load();settings['model']='different-model'
            with llm_settings.use(settings):
                chat.reset_mock();self.run_summary()
                self.assertEqual(chat.call_count,5)
            with db.connect() as con:
                con.execute("UPDATE jobs SET status='done' WHERE id=?",(self.ident,))
            self.ident=worker.enqueue('process',{'force':True},ID)
            chat.reset_mock();self.run_summary()
            self.assertEqual(chat.call_count,5)

    def test_invalid_output_is_never_checkpointed(self):
        with patch('app.summarize.chat',return_value={'summary':'English translation'}):
            with self.assertRaises(summarize.ModelOutputError):
                self.run_summary()
        with db.connect() as con:
            self.assertEqual(con.execute('SELECT count(*) FROM summary_checkpoints').fetchone()[0],0)

    def test_changed_upstream_result_does_not_reuse_stale_merge(self):
        with patch('app.summarize.chat',return_value=response('原来的中文结论')):
            self.run_summary()
        with db.connect() as con:
            con.execute("DELETE FROM summary_checkpoints WHERE stage LIKE 'section 1:%'")
        with patch('app.summarize.chat',return_value=response('新的中文结论')) as chat:
            result=self.run_summary()
        self.assertEqual(chat.call_count,2)  # changed section and dependent merge
        self.assertEqual(result['summary'],'新的中文结论')
