"""SQLite storage, settings, personal vocabulary, and Windows-protected secrets."""
from contextlib import contextmanager
import ctypes
import difflib
import os
import re
import sqlite3
import threading
import time

from . import paths


AUTH_LOCK = threading.RLock()


@contextmanager
def database(vocabulary=False):
    db = sqlite3.connect(paths.DATA / 'relay.sqlite3', timeout=30)
    try:
        db.row_factory = sqlite3.Row
        db.execute('CREATE TABLE IF NOT EXISTS approvals (id TEXT PRIMARY KEY, payload TEXT NOT NULL, created REAL NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS deliveries (approval TEXT, thread TEXT, state TEXT NOT NULL, receipt TEXT, PRIMARY KEY (approval, thread))')
        db.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value INTEGER NOT NULL)')
        if vocabulary:
            db.execute('ATTACH DATABASE ? AS learned', (str(paths.DATA / 'vocabulary.sqlite3'),))
            with db:
                # Move legacy rows atomically; keep settings and delivery history in the main database.
                db.execute('BEGIN IMMEDIATE')
                db.execute('CREATE TABLE IF NOT EXISTS learned.vocabulary (word TEXT PRIMARY KEY, uses INTEGER NOT NULL, updated REAL NOT NULL)')
                db.execute('CREATE TABLE IF NOT EXISTS learned.corrections (heard TEXT PRIMARY KEY, spelling TEXT NOT NULL, updated REAL NOT NULL)')
                for table, key, values in [('vocabulary', 'word', 'uses=excluded.uses, updated=excluded.updated'),
                                           ('corrections', 'heard', 'spelling=excluded.spelling, updated=excluded.updated')]:
                    if db.execute("SELECT 1 FROM main.sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                        db.execute(f'INSERT INTO learned.{table} SELECT * FROM main.{table} WHERE true '
                                   f'ON CONFLICT({key}) DO UPDATE SET {values} WHERE excluded.updated > {table}.updated')
                        db.execute(f'DROP TABLE main.{table}')
        with db:
            yield db
    finally:
        db.close()


ENGLISH_WORD = r'[A-Za-z][A-Za-z0-9_./:+#@-]*'


def get_setting(key, default=None):
    with database() as db:
        row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
    return default if row is None else row['value']


def set_setting(key, value):
    with database() as db:
        db.execute('INSERT INTO settings VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, value))


def self_learning_enabled():
    return bool(get_setting('self_learning', 1))


def set_self_learning(enabled):
    if type(enabled) is not bool:
        raise ValueError('Self-learning must be enabled or disabled.')
    set_setting('self_learning', int(enabled))
    return {'selfLearning': enabled}


def personal_vocabulary():
    with database(vocabulary=True) as db:
        words = [row['word'] for row in db.execute('SELECT word FROM learned.vocabulary ORDER BY uses DESC, updated DESC, word LIMIT 300')]
        corrections = [dict(row) for row in db.execute('SELECT heard, spelling FROM learned.corrections ORDER BY updated DESC, heard LIMIT 100')]
    return {'words': words, 'corrections': corrections}


def auto_spelling_enabled():
    return bool(get_setting('auto_spelling', 0))


def set_auto_spelling(enabled):
    if type(enabled) is not bool:
        raise ValueError('Automatic spelling correction must be enabled or disabled.')
    set_setting('auto_spelling', int(enabled))
    return {'autoSpelling': enabled}


def learn_vocabulary(reviewed, original=''):
    # Only the reviewed source teaches vocabulary; the generated translation cannot teach itself.
    words = {word.rstrip('.:/') for word in re.findall(ENGLISH_WORD, reviewed)}
    words = {word for word in words if 2 <= len(word) <= 80}
    corrections = []
    tokens = r'[\u0621-\u064a\u0660-\u0669\u0671-\u06d3ـ]+|' + ENGLISH_WORD
    before, after = re.findall(tokens, original), re.findall(tokens, reviewed)
    if original:
        for kind, a, b, c, d in difflib.SequenceMatcher(None, before, after, autojunk=False).get_opcodes():
            old, new = before[a:b], after[c:d]
            # Learn short substitutions, not rewritten sentences or Arabic translations.
            english = [word for word in new if re.fullmatch(ENGLISH_WORD, word)]
            prefixes = {'ال', 'الـ', 'ت', 'و', 'ب'}
            if (kind == 'replace' and 1 <= len(old) <= 3 and 1 <= len(english) <= 3
                    and all(not re.search('[A-Za-z]', word) for word in old)
                    and all(word in english or word in prefixes for word in new)):
                heard, spelling = ' '.join(old), ' '.join(english).rstrip('.:/')
                if len(heard) <= 80 and len(spelling) <= 80:
                    corrections.append((heard, spelling))
    now = time.time()
    with database(vocabulary=True) as db:
        # Serialize the setting check with writes so a completed disable cannot be bypassed by an in-flight request.
        db.execute('BEGIN IMMEDIATE')
        setting = db.execute("SELECT value FROM settings WHERE key='self_learning'").fetchone()
        if setting is None or setting['value']:
            db.executemany('INSERT INTO learned.vocabulary VALUES (?, 1, ?) ON CONFLICT(word) DO UPDATE SET uses=uses+1, updated=excluded.updated',
                           [(word, now) for word in words])
            db.executemany('INSERT INTO learned.corrections VALUES (?, ?, ?) ON CONFLICT(heard) DO UPDATE SET spelling=excluded.spelling, updated=excluded.updated',
                           [(heard, spelling, now) for heard, spelling in corrections])
            db.execute('DELETE FROM learned.vocabulary WHERE word NOT IN (SELECT word FROM learned.vocabulary ORDER BY uses DESC, updated DESC, word LIMIT 300)')
            db.execute('DELETE FROM learned.corrections WHERE heard NOT IN (SELECT heard FROM learned.corrections ORDER BY updated DESC, heard LIMIT 100)')
    return personal_vocabulary()


def forget_vocabulary():
    with database(vocabulary=True) as db:
        db.execute('DELETE FROM learned.vocabulary')
        db.execute('DELETE FROM learned.corrections')
    return {'words': [], 'corrections': []}


class Blob(ctypes.Structure):
    _fields_ = [('length', ctypes.c_ulong), ('data', ctypes.POINTER(ctypes.c_byte))]


def protect(data, decrypt=False):
    if os.name != 'nt':
        raise ValueError('This version stores its T3 credentials using Windows encryption.')
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    result = Blob()
    crypto = ctypes.windll.crypt32
    fn = crypto.CryptUnprotectData if decrypt else crypto.CryptProtectData
    if not fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise ValueError('Windows could not access this app’s saved sign-in.')
    try:
        return ctypes.string_at(result.data, result.length)
    finally:
        ctypes.windll.kernel32.LocalFree(result.data)
