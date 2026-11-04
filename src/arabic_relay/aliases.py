"""Approved vocabulary aliases: deterministic replacements of a heard Arabic-script form with its English spelling.

Only rules the user explicitly approved replace text. Learned corrections stay proposals until approved; hints stay
hints. Matching is exact on whole words (Unicode-aware), longest first, with a small set of Arabic prefixes kept in
front of the English term. Crossing matches of different rules are reported as conflicts and left unchanged.
The original text is always returned alongside the corrected text. This does not train or fine-tune Whisper.
"""
import re
import time
import unicodedata as ud

from . import inline, store

LIMIT = 300
# Prefixes that may stay attached in front of a heard form: articles first (longest first), then single letters.
ARTICLES = ('وال', 'بال', 'فال', 'كال', 'لل', 'ال')
LETTERS = ('و', 'ب', 'ل', 'ف', 'ك', 'ت')
FOLD = str.maketrans({'أ': 'ا', 'إ': 'ا', 'آ': 'ا', 'ٱ': 'ا', 'ى': 'ي', 'ة': 'ه'})
CANONICAL = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._+#/:'-]{0,79}")


def arabic_letter(c):
    return 'ARABIC' in ud.name(c, '') and ud.category(c) == 'Lo'


def word_char(c):
    return ud.category(c)[0] in 'LN' or ud.category(c) == 'Mn'


def fold(text):
    """Folded text for matching plus, for each folded character, its index in the original."""
    out, index = [], []
    for position, c in enumerate(text):
        if c == 'ـ' or ud.category(c) == 'Mn':      # tatweel and diacritics never decide a match
            continue
        out.append(c.translate(FOLD))
        index.append(position)
    return ''.join(out), index


def clean_heard(heard):
    if not isinstance(heard, str):
        raise ValueError('اكتب الكلمة زي ما اتسمعت بحروف عربي.')
    heard = ' '.join(heard.split())
    folded = fold(heard)[0]
    if not 2 <= len(heard) <= 80 or not any(map(arabic_letter, heard)) \
            or any(not (arabic_letter(c) or c == ' ') for c in folded):
        raise ValueError('الكلمة المسموعة لازم تكون من حرفين لـ 80 حرف عربي، زي ريدزاين.')
    return heard


def clean_canonical(canonical):
    if not isinstance(canonical, str):
        raise ValueError('اكتب الكتابة الإنجليزي.')
    canonical = ' '.join(canonical.split())
    if not CANONICAL.fullmatch(canonical) or not re.search('[A-Za-z]', canonical):
        raise ValueError('الكتابة الإنجليزي لازم تكون من حرف لـ 80 حرف لاتيني أو أرقام أو . _ - + # / :')
    return canonical


def rows(db=None):
    def read(db):
        return [dict(row) for row in db.execute('SELECT id, heard, canonical, enabled, scope, provenance, approved, updated '
                                                 'FROM learned.aliases ORDER BY lower(canonical), heard')]
    if db is not None:
        return read(db)
    with store.database(vocabulary=True) as db:
        return read(db)


def listing():
    with store.database(vocabulary=True) as db:
        aliases = rows(db)
        heard = {fold(row['heard'])[0] for row in aliases}
        # Learned corrections are proposals: shown for approval, never applied until approved.
        proposals = [dict(row) for row in db.execute('SELECT heard, spelling FROM learned.corrections ORDER BY updated DESC LIMIT 100')]
    proposals = [p for p in proposals if fold(' '.join(p['heard'].split()))[0] not in heard and valid_pair(p['heard'], p['spelling'])]
    return {'aliases': aliases, 'proposals': proposals}


def valid_pair(heard, canonical):
    try:
        clean_heard(heard)
        clean_canonical(canonical)
        return True
    except ValueError:
        return False


def add(heard, canonical, provenance='user'):
    heard, canonical = clean_heard(heard), clean_canonical(canonical)
    if provenance not in ('user', 'learned'):
        raise ValueError('الموافقة مش صحيحة.')
    now = time.time()
    with store.database(vocabulary=True) as db:
        db.execute('BEGIN IMMEDIATE')
        existing = {fold(row['heard'])[0]: row for row in rows(db)}
        if fold(heard)[0] in existing:
            raise ValueError(f"“{heard}” ليها قاعدة بالفعل. عدّل القاعدة دي.")
        if db.execute('SELECT COUNT(*) FROM learned.aliases').fetchone()[0] >= LIMIT:
            raise ValueError(f'أقصى عدد {LIMIT} قاعدة.')
        db.execute('INSERT INTO learned.aliases (heard, canonical, enabled, scope, provenance, approved, updated) '
                   "VALUES (?, ?, 1, 'global', ?, ?, ?)", (heard, canonical, provenance, now, now))
    return listing()


def update(rule_id, heard=None, canonical=None, enabled=None):
    if type(rule_id) is not int:
        raise ValueError('اختار قاعدة.')
    with store.database(vocabulary=True) as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM learned.aliases WHERE id=?', (rule_id,)).fetchone()
        if row is None:
            raise ValueError('القاعدة دي مش موجودة.')
        heard = clean_heard(heard) if heard is not None else row['heard']
        canonical = clean_canonical(canonical) if canonical is not None else row['canonical']
        if enabled is not None and type(enabled) is not bool:
            raise ValueError('اختار القاعدة شغالة ولا لأ.')
        if any(fold(other['heard'])[0] == fold(heard)[0] and other['id'] != rule_id for other in rows(db)):
            raise ValueError(f"“{heard}” ليها قاعدة بالفعل. عدّل القاعدة دي.")
        db.execute('UPDATE learned.aliases SET heard=?, canonical=?, enabled=?, updated=? WHERE id=?',
                   (heard, canonical, int(row['enabled'] if enabled is None else enabled), time.time(), rule_id))
    return listing()


def delete(rule_id):
    if type(rule_id) is not int:
        raise ValueError('اختار قاعدة.')
    with store.database(vocabulary=True) as db:
        db.execute('DELETE FROM learned.aliases WHERE id=?', (rule_id,))
    return listing()


def quoted_ranges(text):
    pairs = (('"', '"'), ('«', '»'), ('“', '”'), ('`', '`'))
    ranges = [(m.start(), m.end()) for a, b in pairs for m in re.finditer(re.escape(a) + '[^' + re.escape(b) + r'\n]*' + re.escape(b), text)]
    ranges += [(p['start_cp'], p['end_cp']) for p in inline.protected_ranges(text)]
    return ranges


def candidates(text, rules):
    folded, index = fold(text)
    found = []
    for rule in rules:
        target = fold(' '.join(rule['heard'].split()))[0]
        pattern = r'\s+'.join(map(re.escape, target.split(' ')))
        for match in re.finditer(pattern, folded):
            begin, end = match.start(), match.end()
            if end < len(folded) and word_char(folded[end]):
                continue                     # no suffixes: ريدزاينات is not ريدزاين
            prefix = ''
            if begin and word_char(folded[begin - 1]):
                prefix = next((p for p in ARTICLES + LETTERS if folded[max(0, begin - len(p)):begin] == p
                               and (begin - len(p) == 0 or not word_char(folded[begin - len(p) - 1]))), None)
                if prefix is None:
                    continue                 # the heard form is the end of another word
            start = index[begin - len(prefix)]
            stop = index[end - 1] + 1
            found.append({'start': start, 'end': stop, 'prefix': text[start:index[begin]], 'article': prefix in ARTICLES,
                          'rule': rule})
    return found


def apply(text, rules=None):
    """{'text', 'original', 'replacements', 'conflicts'}; replacements carry original offsets and rule IDs."""
    if not isinstance(text, str) or len(text) > 12000:
        raise ValueError('النص لازم يكون أقل من 12,000 حرف.')
    if rules is None:
        rules = [row for row in rows() if row['enabled']]
    found = candidates(text, rules)
    protected = quoted_ranges(text)
    found = [f for f in found if not any(f['start'] < b and f['end'] > a for a, b in protected)]
    # Longest first; a shorter match inside a longer one yields. Crossing matches of different rules are a
    # conflict: neither applies, and nothing else inside that region changes.
    found.sort(key=lambda f: (-(f['end'] - f['start']), f['start']))
    overlaps = lambda a, b: a['start'] < b['end'] and a['end'] > b['start']
    chosen, conflicts = [], []
    for item in found:
        if any(overlaps(item, region) for region in conflicts):
            continue
        clash = [c for c in chosen if overlaps(item, c)]
        if not clash:
            chosen.append(item)
        elif not all(c['start'] <= item['start'] and item['end'] <= c['end'] for c in clash):
            for c in clash:
                chosen.remove(c)
            conflicts.append({'start': min([item['start']] + [c['start'] for c in clash]),
                              'end': max([item['end']] + [c['end'] for c in clash]),
                              'rules': sorted({item['rule']['id'], *(c['rule']['id'] for c in clash)})})
    pieces, cursor, replacements, shift = [], 0, [], 0
    for item in sorted(chosen, key=lambda c: c['start']):
        canonical, prefix = item['rule']['canonical'], item['prefix'].rstrip('ـ')
        value = (prefix + ('ـ ' if item['article'] else ' ') + canonical) if prefix else canonical
        pieces += [text[cursor:item['start']], value]
        replacements.append({'start': item['start'], 'end': item['end'], 'outputStart': item['start'] + shift,
                             'outputEnd': item['start'] + shift + len(value), 'heard': text[item['start']:item['end']],
                             'replacement': value, 'canonical': canonical, 'ruleId': item['rule']['id']})
        shift += len(value) - (item['end'] - item['start'])
        cursor = item['end']
    conflicts = [{'heard': text[c['start']:c['end']], 'rules': c['rules']} for c in conflicts]
    corrected = ''.join(pieces) + text[cursor:]
    return {'text': corrected, 'original': text, 'replacements': replacements, 'conflicts': conflicts}
