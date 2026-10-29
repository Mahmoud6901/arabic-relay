"""The latest twenty accepted send actions. Local text and destination names only."""
import json
import time

from . import store

LIMIT = 20


def _insert(db, request_id, payload, sent_at):
    names = [target['title'] for target in payload['targets'] if isinstance(target.get('title'), str)]
    db.execute('INSERT OR IGNORE INTO sent_history VALUES (?, ?, ?, ?)',
               (request_id, payload['english'], sent_at, json.dumps(names, ensure_ascii=False)))
    db.execute('DELETE FROM sent_history WHERE id NOT IN '
               '(SELECT id FROM sent_history ORDER BY sent_at DESC,id DESC LIMIT ?)', (LIMIT,))


def initialize(db):
    db.execute('CREATE TABLE IF NOT EXISTS sent_history '
               '(id TEXT PRIMARY KEY, english TEXT NOT NULL, sent_at REAL NOT NULL, destinations TEXT NOT NULL)')
    if db.execute("SELECT 1 FROM settings WHERE key='sent_history_imported'").fetchone():
        return
    # Import once: deleted and pruned entries must never return from older delivery snapshots.
    rows = db.execute("SELECT a.id,a.payload,a.created FROM approvals a WHERE EXISTS "
                      "(SELECT 1 FROM deliveries d WHERE d.approval=a.id AND d.state IN ('sent','submitted')) "
                      "ORDER BY a.created DESC,a.id DESC LIMIT ?", (LIMIT,)).fetchall()
    for row in rows:
        try:
            payload = json.loads(row['payload'])
            if (not isinstance(payload.get('english'), str) or not payload['english'].strip()
                    or len(payload['english']) > 16000 or not isinstance(payload.get('targets'), list)
                    or not all(isinstance(target, dict) for target in payload['targets'])):
                continue
            _insert(db, row['id'], payload, row['created'])
        except (ValueError, TypeError, AttributeError, KeyError):
            continue
    db.execute("INSERT INTO settings VALUES ('sent_history_imported',1)")


def record(db, request_id, payload):
    _insert(db, request_id, payload, time.time())


def entries():
    with store.database() as db:
        db.execute('BEGIN IMMEDIATE')
        initialize(db)
        rows = db.execute('SELECT * FROM sent_history ORDER BY sent_at DESC,id DESC LIMIT ?', (LIMIT,)).fetchall()
    return {'entries': [{'id': row['id'], 'english': row['english'], 'sentAt': row['sent_at'],
                         'destinations': json.loads(row['destinations'])} for row in rows]}


def delete(entry_id):
    if not isinstance(entry_id, str) or not entry_id or len(entry_id) > 128:
        raise ValueError('Choose a history entry to delete.')
    with store.database() as db:
        db.execute('BEGIN IMMEDIATE')
        initialize(db)
        db.execute('DELETE FROM sent_history WHERE id=?', (entry_id,))
    return entries()


def clear():
    with store.database() as db:
        db.execute('BEGIN IMMEDIATE')
        initialize(db)
        db.execute('DELETE FROM sent_history')
    return {'entries': []}
