"""Positive, conservative layout rules for tables, charts, and marked lists.

Coordinates are normalized to a 640x360 analysis image. OCR is evidence, not
semantic understanding; uncertain layouts are intentionally left out.
"""
import csv
import io
import re

import numpy as np

W, H = 640, 360
NUMBER = re.compile(r'^[−+\-]?[\d,.]+[%$€万亿kmbKMB]*$')
MARKER = re.compile(r'^(?:([0-9]{1,2})[.)、]|[•●▪◦‣·*\-–])$')


def words_from_tsv(tsv):
    rows = list(csv.DictReader(io.StringIO(tsv), delimiter='\t'))
    page = next((r for r in rows if r['level'] == '1'), None)
    if page is None:
        return []
    sx, sy = W / max(1, int(page['width'])), H / max(1, int(page['height']))
    words = []
    for row in rows:
        text = row.get('text', '').strip()
        if row['level'] != '5' or not text or float(row['conf']) < 25:
            continue
        word = {'text': text, 'x': int(row['left']) * sx, 'y': int(row['top']) * sy,
                'w': int(row['width']) * sx, 'h': int(row['height']) * sy}
        if word['h'] >= 3:
            words.append(word)
    return words


def text_rows(words):
    rows = []
    for word in sorted(words, key=lambda v: v['y'] + v['h'] / 2):
        center = word['y'] + word['h'] / 2
        if rows and abs(center - rows[-1]['center']) <= max(3, word['h'] * .45):
            rows[-1]['words'].append(word)
        else:
            rows.append({'center': center, 'words': [word]})
    for row in rows:
        row['words'].sort(key=lambda v: v['x'])
    return rows


def bounds(words):
    return (min(w['x'] for w in words), min(w['y'] for w in words),
            max(w['x'] + w['w'] for w in words), max(w['y'] + w['h'] for w in words))


def large_region(box, min_height=.08):
    x1, y1, x2, y2 = box
    return x2 - x1 >= W * .22 and y2 - y1 >= H * min_height and (x2-x1)*(y2-y1) >= W*H*.025


def marked_list(rows):
    items = []
    for row in rows:
        words = row['words']
        match = MARKER.fullmatch(words[0]['text'])
        if match and len(words) >= 2 and sum(len(w['text']) for w in words[1:]) >= 4:
            items.append((words, int(match[1]) if match[1] else None))
    for start in range(len(items) - 2):
        group = items[start:start+3]
        words = [w for item, _ in group for w in item]
        markers = [item[0]['x'] for item, _ in group]
        indents = [item[1]['x'] for item, _ in group]
        numbers = [number for _, number in group]
        aligned = max(markers)-min(markers) <= 12 and max(indents)-min(indents) <= 16
        ordered = numbers == [None]*3 or (None not in numbers and numbers == list(range(numbers[0], numbers[0]+3)))
        gaps = [group[i+1][0][0]['y']-group[i][0][0]['y'] for i in range(2)]
        if aligned and ordered and min(gaps) >= 8 and max(gaps) <= 100 and large_region(bounds(words), .12):
            return bounds(words)
    return None


def table(rows):
    # Large whitespace separates cells; normal word spacing is not a column.
    cell_rows = []
    for row in rows:
        cells = []
        for word in row['words']:
            if not cells or word['x'] - (cells[-1][-1]['x']+cells[-1][-1]['w']) > max(16, word['h']*1.5):
                cells.append([word])
            else:
                cells[-1].append(word)
        if len(cells) >= 2 and not MARKER.fullmatch(cells[0][0]['text']):
            cell_rows.append(cells)
    for top, bottom in zip(cell_rows, cell_rows[1:]):
        if not 10 <= bottom[0][0]['y'] - top[0][0]['y'] <= 100:
            continue
        matched = []
        used = set()
        for cell in top:
            a = bounds(cell)
            for index, other in enumerate(bottom):
                if index in used:
                    continue
                b = bounds(other)
                # Support left-, right-, and center-aligned columns.
                if min(abs(a[0]-b[0]), abs(a[2]-b[2]), abs((a[0]+a[2]-b[0]-b[2])/2)) <= 10:
                    matched.append((cell, other))
                    used.add(index)
                    break
        if len(matched) >= 2:
            words = [w for pair in matched for cell in pair for w in cell]
            box = bounds(words)
            # At least two genuinely separated columns, not four nearby words.
            if large_region(box) and max(bounds(pair[0])[0] for pair in matched)-min(bounds(pair[0])[0] for pair in matched) >= W*.12:
                return box
    return None


def long_lines(mask, horizontal=True):
    data = mask if horizontal else mask.T
    minimum = W*.22 if horizontal else H*.20
    found = []
    for index, line in enumerate(data):
        # Bridge tiny compression gaps without merging unrelated text lines.
        line = line.copy()
        line[1:-1] |= line[:-2] & line[2:]
        edges = np.diff(np.r_[False, line, False].astype(np.int8))
        for start, end in zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)):
            if end-start >= minimum:
                found.append((index, start, end))
    return found


def graph(gray, words):
    # Dominant background supports both light and dark slide canvases.
    background = np.median(gray)
    ink = np.abs(gray.astype(float)-background) > 40
    # Axes/gridlines can be much fainter than the plotted data.
    axes = np.abs(gray.astype(float)-background) > 15
    horizontal, vertical = long_lines(axes), long_lines(axes, False)
    numeric = [w for w in words if NUMBER.fullmatch(w['text'])]
    best = None
    for y, left, right in horizontal:
        for x, top, bottom in vertical:
            if min(abs(x-left), abs(x-right)) > 8 or min(abs(y-top), abs(y-bottom)) > 8:
                continue
            if (right-left)*(bottom-top) < W*H*.06:
                continue
            ticks = [w for w in numeric if
                     (min(abs(w['x']+w['w']-x), abs(w['x']-x)) < 30 and top-10 <= w['y'] <= bottom+10)
                     or (abs(w['y']-y) < 25 and left-10 <= w['x'] <= right+10)]
            if len(ticks) < 3:
                continue
            interior = ink[top+8:bottom-8, left+8:right-8].copy()
            if not interior.size:
                continue
            # Gridlines/borders by themselves are not plotted data.
            interior[np.mean(interior, axis=1) > .70, :] = False
            interior[:, np.mean(interior, axis=0) > .70] = False
            ys, xs = np.nonzero(interior)
            if len(xs) >= 30 and .003 <= np.mean(interior) <= .35 and np.ptp(xs) >= interior.shape[1]*.3 and np.ptp(ys) >= interior.shape[0]*.15:
                region = (left, top, right, bottom)
                if best is None or (right-left)*(bottom-top) > (best[2]-best[0])*(best[3]-best[1]):
                    best = region
    return best


def pie(gray, words):
    if sum('%' in w['text'] and any(c.isdigit() for c in w['text']) for w in words) < 2:
        return None
    # Search for a filled circular region with contrasting sectors, not an icon.
    bg = np.median(gray)
    for radius in (45, 60, 80, 100, 120):
        yy, xx = np.mgrid[-radius:radius+1, -radius:radius+1]
        disk = xx*xx+yy*yy <= radius*radius
        corners = xx*xx+yy*yy > (radius*1.12)**2
        for cy in range(radius, H-radius, 15):
            for cx in range(radius, W-radius, 15):
                crop = gray[cy-radius:cy+radius+1, cx-radius:cx+radius+1]
                foreground = np.abs(crop.astype(float)-bg) > 30
                if np.mean(foreground[disk]) > .80 and np.mean(foreground[corners]) < .12 and np.std(crop[disk]) > 18:
                    return (cx-radius, cy-radius, cx+radius, cy+radius)
    return None


def classify(gray, words):
    rows = text_rows(words)
    # Navigation controls are not teaching lists, even if numbered.
    navigation = {'home', 'settings', 'notifications', 'messages', 'subscriptions', '首页', '设置', '通知', '訂閱'}
    if len(navigation.intersection(w['text'].casefold() for w in words)) >= 3:
        return None
    for kind, detector in [('list', lambda: marked_list(rows)),
                           ('chart', lambda: graph(gray, words)),
                           ('table', lambda: table(rows)),
                           ('chart', lambda: pie(gray, words))]:
        region = detector()
        if region is not None:
            return {'kind': kind, 'region': [round(v, 1) for v in region]}
    return None
