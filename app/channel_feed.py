"""Read YouTube's public Atom channel feed without per-video metadata requests."""
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from . import media

CHANNEL_ID = re.compile(r'UC[A-Za-z0-9_-]{22}')
NS = {'a': 'http://www.w3.org/2005/Atom', 'yt': 'http://www.youtube.com/xml/schemas/2015'}


def read(channel_id):
    if not CHANNEL_ID.fullmatch(channel_id):
        raise ValueError('Invalid YouTube channel ID.')
    url = 'https://www.youtube.com/xml/feeds/videos.xml?channel_id=' + channel_id
    with urlopen(Request(url, headers={'User-Agent': 'Noter/1.0'}), timeout=20) as response:
        content = response.read(1_000_001)
    if len(content) > 1_000_000 or b'<!DOCTYPE' in content.upper() or b'<!ENTITY' in content.upper():
        raise ValueError('Invalid channel feed.')
    root = ET.fromstring(content)
    if root.tag != '{' + NS['a'] + '}feed' or root.findtext('yt:channelId', namespaces=NS) != channel_id:
        raise ValueError('Unexpected channel feed.')
    videos = []
    for entry in root.findall('a:entry', NS):
        ident = entry.findtext('yt:videoId', namespaces=NS) or ''
        if not media.VIDEO_ID.fullmatch(ident):
            raise ValueError('Invalid feed video ID.')
        date = datetime.fromisoformat((entry.findtext('a:published', namespaces=NS) or '').replace('Z', '+00:00'))
        if date.tzinfo is None:
            raise ValueError('Feed publication timestamp has no timezone.')
        videos.append({'id': ident, 'url': f'https://www.youtube.com/watch?v={ident}',
                       'title': entry.findtext('a:title', namespaces=NS) or ident,
                       'creator': entry.findtext('a:author/a:name', namespaces=NS) or '',
                       'channel_id': channel_id, 'published_at': date.astimezone(timezone.utc).isoformat(),
                       'metadata': {'date_precision': 'timestamp'}})
    return videos
