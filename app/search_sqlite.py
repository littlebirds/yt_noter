"""SQLite search adapter: trigram/short-term indexes, filters, and newest-first ordering."""

def sync(con, video_id):
    """Maintain search indexes in the same transaction as the library write."""
    row = con.execute("""SELECT v.rowid,v.title,COALESCE(t.text,''),COALESCE(s.markdown,'')
        FROM videos v LEFT JOIN transcripts t ON t.video_id=v.id
        LEFT JOIN summaries s ON s.video_id=v.id WHERE v.id=?""", (video_id,)).fetchone()
    con.execute('DELETE FROM search_short_terms WHERE video_id=?', (video_id,))
    if not row:
        return
    con.execute('DELETE FROM video_search WHERE rowid=?', (row[0],))
    if not con.execute('SELECT 1 FROM library_items WHERE video_id=?', (video_id,)).fetchone():
        return
    con.execute('INSERT INTO video_search(rowid,title,transcript,summary) VALUES(?,?,?,?)', tuple(row))
    # SQLite LIKE folds ASCII only. Preserve the same matching behavior.
    fold = str.maketrans('ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')
    terms = set()
    for value in row[1:]:
        value = value.translate(fold)
        terms.update(value)
        terms.update(value[i:i+2] for i in range(len(value)-1))
    con.executemany('INSERT INTO search_short_terms(term,video_id) VALUES(?,?)',
                    ((term, video_id) for term in terms))


def initialize(con):
    if not con.execute('SELECT 1 FROM schema_version WHERE version=5').fetchone():
        con.execute("CREATE VIRTUAL TABLE video_search USING fts5(title,transcript,summary,tokenize='trigram')")
        con.execute('CREATE TABLE search_short_terms(term TEXT NOT NULL,video_id TEXT NOT NULL REFERENCES videos(id),PRIMARY KEY(term,video_id)) WITHOUT ROWID')
        con.execute('CREATE INDEX search_short_video ON search_short_terms(video_id)')
        for row in con.execute('SELECT video_id FROM library_items').fetchall():
            sync(con, row[0])
        con.execute('INSERT INTO schema_version VALUES(5)')

def find(con, filters, limit=1000):
    clauses, params = ['EXISTS(SELECT 1 FROM library_items l WHERE l.video_id=v.id)'], []
    for name, column in [('creator', 'v.creator'), ('category', 'v.category'), ('language', 'v.language')]:
        if filters.get(name):
            clauses.append(f'{column} = ?')
            params.append(filters[name])
    if filters.get('q'):
        text = filters['q']
        if len(text) >= 3:
            clauses.append('v.rowid IN (SELECT rowid FROM video_search WHERE video_search MATCH ?)')
            params.append('"' + text.replace('"', '""') + '"')
        else:
            clauses.append('v.id IN (SELECT video_id FROM search_short_terms WHERE term=?)')
            params.append(text.translate(str.maketrans('ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')))
        # Verify literal substring semantics only on indexed candidates.
        clauses.append("(v.title LIKE ? ESCAPE '\\' OR t.text LIKE ? ESCAPE '\\' OR s.markdown LIKE ? ESCAPE '\\')")
        query = '%' + filters['q'].replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
        params.extend([query] * 3)
    if filters.get('ticker'):
        clauses.append('EXISTS(SELECT 1 FROM mentions m WHERE m.video_id=v.id AND m.ticker=?)')
        params.append(filters['ticker'].upper().lstrip('$'))
    for name, op in [('from', '>='), ('to', '<=')]:
        if filters.get(name):
            clauses.append(f'substr(v.published_at,1,10) {op} ?')
            params.append(filters[name][:10])
    where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
    rows = con.execute('''SELECT v.id FROM videos v
            LEFT JOIN transcripts t ON t.video_id=v.id
            LEFT JOIN summaries s ON s.video_id=v.id''' + where +
            ' ORDER BY COALESCE(v.published_at,v.created_at) DESC,v.id LIMIT ?', params + [limit]).fetchall()
    return [row['id'] for row in rows]


class SQLiteSearch:
    initialize = staticmethod(initialize)
    sync = staticmethod(sync)
    find = staticmethod(find)
