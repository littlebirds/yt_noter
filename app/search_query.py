"""Shared filters and ordering for Library search."""

ASCII_UPPER = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
ASCII_LOWER = str.maketrans(ASCII_UPPER, ASCII_UPPER.lower())


def find_videos(con, filters, text_filter, limit):
    clauses, params = ['EXISTS(SELECT 1 FROM library_items l WHERE l.video_id=v.id)'], []
    for name, column in [('creator', 'v.creator'), ('category', 'v.category'), ('language', 'v.language')]:
        if filters.get(name):
            clauses.append(f'{column} = ?')
            params.append(filters[name])
    if filters.get('q'):
        text_clauses, text_params = text_filter(filters['q'])
        clauses.extend(text_clauses)
        params.extend(text_params)
    if filters.get('ticker'):
        clauses.append('EXISTS(SELECT 1 FROM mentions m WHERE m.video_id=v.id AND m.ticker=?)')
        params.append(filters['ticker'].upper().lstrip('$'))
    for name, op in [('from', '>='), ('to', '<=')]:
        if filters.get(name):
            clauses.append(f'substr(v.published_at,1,10) {op} ?')
            params.append(filters[name][:10])
    where = ' WHERE ' + ' AND '.join(clauses)
    rows = con.execute('''SELECT v.id FROM videos v
            LEFT JOIN transcripts t ON t.video_id=v.id
            LEFT JOIN summaries s ON s.video_id=v.id''' + where +
            ' ORDER BY COALESCE(v.published_at,v.created_at) DESC,v.id LIMIT ?', params + [limit]).fetchall()
    return [row['id'] for row in rows]
