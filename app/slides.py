"""Stable educational candidates with OCR-based conversation-screen exclusion."""
import re
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from . import config, media, slide_content

WIDTH, HEIGHT, FPS = 320, 180, 2


def similar(anchor, frame):
    # Compare to the start of the hold, so slow animations cannot drift forever.
    delta = np.abs(frame.astype(np.int16) - anchor.astype(np.int16))
    return np.mean(delta > 25) <= .015 and np.mean(delta) <= 2.5


def structured(frame):
    """Prefer flat backgrounds with sharp text/diagram edges over photographs."""
    pixels = frame.astype(np.int16)
    dx = np.abs(np.diff(pixels, axis=1))
    dy = np.abs(np.diff(pixels, axis=0))
    edges = (np.mean(dx > 35) + np.mean(dy > 35)) / 2
    flat = (np.mean(dx < 8) + np.mean(dy < 8)) / 2
    return .012 <= edges <= .30 and flat >= .80


def stable_content(anchor, frame):
    if similar(anchor, frame):
        return True
    # Subtitle overlays often change independently of an otherwise static slide.
    # Require structure in the central 76%, not just text in the ignored bands.
    margin = max(1, round(anchor.shape[0] * .12))
    center = anchor[margin:-margin]
    return structured(center) and similar(center, frame[margin:-margin])


def candidates(frames, minimum=4):
    """Yield sampled start/end holds and a representative original-video timestamp."""
    anchor = None
    start = end = 0
    for timestamp, frame in frames:
        if anchor is None:
            anchor, start = frame.copy(), timestamp
        elif not stable_content(anchor, frame):
            if end - start >= minimum and structured(anchor):
                yield {'start': start, 'end': end, 'timestamp': start}
            anchor, start = frame.copy(), timestamp
        end = timestamp
    if anchor is not None and end - start >= minimum and structured(anchor):
        yield {'start': start, 'end': end, 'timestamp': start}


def sample_frames(path, start=0, duration=None):
    command = ['ffmpeg', '-v', 'error', '-ss', str(start), '-i', str(path)]
    if duration is not None:
        command += ['-t', str(duration)]
    command += ['-an', '-vf',
               f'fps={FPS}:start_time=0,scale={WIDTH}:{HEIGHT}', '-pix_fmt', 'gray', '-f', 'rawvideo', 'pipe:1']
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=errors)
        try:
            index = 0
            while True:
                data = process.stdout.read(WIDTH * HEIGHT)
                if not data:
                    break
                if len(data) != WIDTH * HEIGHT:
                    raise RuntimeError('Incomplete video frame while scanning slides.')
                yield index / FPS, np.frombuffer(data, dtype=np.uint8).reshape(HEIGHT, WIDTH)
                index += 1
            if process.wait() != 0:
                raise RuntimeError('ffmpeg could not decode video for slide scanning.')
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
            process.stdout.close()


def conversation_screen(text):
    """Require repeated UI cues, not a single word such as 'comment' in a lesson."""
    text = text.casefold()
    broadcasts = len(re.findall(r'\b(?:everyone|here)\b', text))
    explicit_broadcast = bool(re.search(r'@\s*(?:everyone|here)\b', text))
    channels = len(re.findall(r'^\s*#\s*\S', text, re.MULTILINE))
    replies = len(re.findall(r'\b(?:reply|replies)\b|回复|回覆|返信', text))
    reactions = len(re.findall(r'\b(?:like|likes|react|reactions)\b|点赞|讚', text))
    timestamps = len(re.findall(
        r'\b(?:yesterday|today)\s+at\b|\b\d+\s+(?:seconds?|minutes?|hours?|days?|weeks?|months?|years?)\s+ago\b|'
        r'\d+\s*(?:秒|分钟|分鐘|小时|小時|天|周|週|个月|個月|年)前', text))
    handles = len(re.findall(r'@[\w.-]+', text))
    return ((explicit_broadcast and broadcasts >= 2)
            or (channels >= 5 and handles >= 1)
            or (replies >= 2 and (timestamps >= 2 or reactions >= 2 or handles >= 2)))


def ocr_languages():
    try:
        result = subprocess.run(['tesseract', '--list-langs'], capture_output=True, text=True, timeout=15)
    except FileNotFoundError:
        raise RuntimeError('Slide filtering requires Tesseract OCR. Install tesseract-ocr and its English language data.') from None
    available = set(result.stdout.split())
    if result.returncode or 'eng' not in available:
        raise RuntimeError('Tesseract English language data is unavailable. Install tesseract-ocr-eng.')
    return '+'.join(lang for lang in ('eng', 'chi_sim', 'chi_tra') if lang in available)


def educational_image(image, languages):
    try:
        result = subprocess.run(['tesseract', 'stdin', 'stdout', '-l', languages, '--psm', '11', 'tsv'],
                                input=image, capture_output=True, timeout=60)
    except subprocess.TimeoutExpired:
        raise RuntimeError('Slide content filtering timed out. Retry the scan.') from None
    if result.returncode:
        raise RuntimeError('Slide content filtering failed. Check the Tesseract installation.')
    words = slide_content.words_from_tsv(result.stdout.decode('utf-8', errors='replace'))
    rows = slide_content.text_rows(words)
    text = '\n'.join(' '.join(w['text'] for w in row['words']) for row in rows)
    if conversation_screen(text):
        return None
    # JPEG bytes can falsely probe as MPEG-TS. This input is always a still image.
    decoded = subprocess.run(['ffmpeg', '-v', 'error', '-f', 'image2pipe', '-i', 'pipe:0', '-vf',
                              'scale=640:360', '-frames:v', '1', '-pix_fmt', 'gray',
                              '-f', 'rawvideo', 'pipe:1'], input=image, capture_output=True, timeout=30)
    if decoded.returncode or len(decoded.stdout) != 640*360:
        raise RuntimeError('Could not analyze slide content.')
    gray = np.frombuffer(decoded.stdout, dtype=np.uint8).reshape(360, 640)
    return slide_content.classify(gray, words)


def stable_region(path, timestamp, minimum, region):
    x1, y1, x2, y2 = region
    left, top = max(0, int(x1/2)), max(0, int(y1/2))
    right, bottom = min(WIDTH, int(np.ceil(x2/2))), min(HEIGHT, int(np.ceil(y2/2)))
    anchor = None
    end = 0
    for end, frame in sample_frames(path, timestamp, minimum + 1/FPS):
        crop = frame[top:bottom, left:right]
        if not crop.size:
            return False
        if anchor is None:
            anchor = crop.copy()
        elif not similar(anchor, crop):
            return False
    return anchor is not None and end >= minimum


def extract(video_id, minimum=4, progress=lambda _: None):
    languages = ocr_languages()
    config.DATA.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='slides-', dir=config.DATA) as tmp:
        progress('Downloading video for slide scanning')
        media.run_ytdlp(['--no-playlist', '-f', 'bestvideo[height<=1080]/best[height<=1080]',
                         '--max-filesize', '2G', '-o', str(Path(tmp) / 'video.%(ext)s'),
                         f'https://www.youtube.com/watch?v={video_id}'])
        paths = [p for p in Path(tmp).glob('video.*') if p.suffix not in ('.part', '.ytdl')]
        if len(paths) != 1:
            raise RuntimeError('Video download failed or exceeded the 2 GB limit.')
        def frames():
            for timestamp, frame in sample_frames(paths[0]):
                if timestamp % 30 == 0:
                    progress(f'Scanning stable slides: {int(timestamp)}s')
                yield timestamp, frame
        output = []
        for slide in candidates(frames(), minimum):
            image = subprocess.run(['ffmpeg', '-v', 'error', '-ss', str(slide['timestamp']),
                                    '-i', str(paths[0]), '-frames:v', '1', '-f', 'image2pipe',
                                    '-vcodec', 'mjpeg', '-q:v', '2', 'pipe:1'], capture_output=True, timeout=60)
            if image.returncode or not image.stdout:
                raise RuntimeError('Could not save a slide image. Retry the scan.')
            progress(f'Filtering slide content at {int(slide["timestamp"])}s')
            content = educational_image(image.stdout, languages)
            if content and stable_region(paths[0], slide['timestamp'], minimum, content['region']):
                output.append({**slide, 'image': image.stdout, 'content_kind': content['kind']})
        return output
