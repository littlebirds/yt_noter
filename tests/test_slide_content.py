import unittest
from unittest.mock import patch

import numpy as np

from app import slide_content as content, slides


def word(text, x, y, w=80, h=16):
    return dict(text=text, x=x, y=y, w=w, h=h)


class LayoutTests(unittest.TestCase):
    def setUp(self):
        self.blank = np.full((360, 640), 245, dtype=np.uint8)

    def test_two_by_two_table_with_or_without_borders(self):
        words = [word('Year',100,100),word('Revenue',320,100),
                 word('2025',100,145),word('123.4',320,145)]
        for grid in (False, True):
            gray = self.blank.copy()
            if grid:
                gray[90:180:40, 90:420] = 0
                gray[90:180, 90:421:165] = 0
            self.assertEqual(content.classify(gray, words)['kind'], 'table')

    def test_one_row_one_column_and_tiny_attachment_are_rejected(self):
        examples = [[word('A',100,100),word('B',320,100)],
                    [word('A',100,100),word('B',100,145)],
                    [word('A',100,100,10,4),word('B',130,100,10,4),
                     word('C',100,110,10,4),word('D',130,110,10,4)]]
        for words in examples:
            self.assertIsNone(content.classify(self.blank, words))

    def test_bullets_and_sequential_numbers_with_wrapped_line(self):
        for markers in (['•']*3, ['1.','2.','3.'], ['1、','2、','3、']):
            words = []
            for i, marker in enumerate(markers):
                words += [word(marker,80,80+i*60,10),word('An educational point',110,80+i*60,260)]
            words.append(word('Wrapped continuation',110,110,240))
            self.assertEqual(content.classify(self.blank, words)['kind'], 'list')

    def test_paragraph_nonsequential_numbers_and_navigation_rejected(self):
        paragraph = [word('A normal paragraph',80,80+i*30,280) for i in range(4)]
        self.assertIsNone(content.classify(self.blank, paragraph))
        words = []
        for i, marker in enumerate(['1.','5.','9.']):
            words += [word(marker,80,80+i*60,10),word('An educational point',110,80+i*60,260)]
        self.assertIsNone(content.classify(self.blank, words))
        for i, text in enumerate(['Home','Settings','Notifications']):
            words[2*i]['text'] = str(i+1)+'.'
            words[2*i+1]['text'] = text
        self.assertIsNone(content.classify(self.blank, words))

    def test_graph_needs_axes_ticks_and_plotted_data(self):
        gray = self.blank.copy()
        gray[60:281,100:102] = 0
        gray[279:281,100:550] = 0
        ticks = [word(str(i*10),65,70+i*65,25,12) for i in range(4)]
        self.assertIsNone(content.classify(gray, ticks))
        for x in range(112,540):
            y = int(170 + 65*np.sin(x/40))
            gray[y-1:y+2,x] = 20
        self.assertEqual(content.classify(gray, ticks)['kind'], 'chart')
        self.assertIsNone(content.classify(gray, []))

    def test_chart_with_faint_right_hand_axis(self):
        gray = self.blank.copy()
        gray[60:281,550:552] = 220
        gray[279:281,100:552] = 220
        ticks = [word(str(i*10),560,70+i*65,25,12) for i in range(4)]
        self.assertIsNone(content.classify(gray,ticks))
        for x in range(112,540):
            y = int(170 + 65*np.sin(x/40))
            gray[y-1:y+2,x] = 20
        self.assertEqual(content.classify(gray,ticks)['kind'],'chart')

    def test_labelled_pie_sectors(self):
        gray = self.blank.copy()
        yy,xx = np.mgrid[:360,:640]
        disk = (xx-300)**2+(yy-180)**2 <= 80**2
        gray[disk & (xx<300)] = 80
        gray[disk & (xx>=300)] = 150
        words = [word('40%',200,80,35),word('60%',380,240,35)]
        self.assertEqual(content.classify(gray,words)['kind'], 'chart')
        self.assertIsNone(content.classify(gray,[]))

    def test_region_motion_is_rejected_even_when_global_motion_is_small(self):
        frames = []
        for i in range(9):
            frame = np.full((180,320),245,dtype=np.uint8)
            frame[55:60,55+i:65+i] = 0
            frames.append((i/2,frame))
        self.assertTrue(slides.similar(frames[0][1],frames[-1][1]))
        with patch('app.slides.sample_frames',return_value=iter(frames)):
            self.assertFalse(slides.stable_region('video',0,4,[100,100,160,140]))
        with patch('app.slides.sample_frames',return_value=iter([(i/2,frames[0][1]) for i in range(9)])):
            self.assertTrue(slides.stable_region('video',0,4,[100,100,160,140]))
        with patch('app.slides.sample_frames',return_value=iter(frames[:8])):
            self.assertFalse(slides.stable_region('video',0,4,[200,200,260,240]))

    def test_tsv_scales_and_filters_unreadable_words(self):
        tsv = 'level\tleft\ttop\twidth\theight\tconf\ttext\n1\t0\t0\t1280\t720\t-1\t\n5\t200\t100\t80\t30\t90\tRevenue\n5\t400\t100\t80\t30\t0\tnoise\n'
        self.assertEqual(content.words_from_tsv(tsv), [word('Revenue',100,50,40,15)])
