"""Approved vocabulary aliases: migration, CRUD, approval, Unicode boundaries, prefixes, overlaps and protected text."""
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from arabic_relay import aliases, paths, store


def rule(rule_id, heard, canonical):
    return {'id': rule_id, 'heard': heard, 'canonical': canonical, 'enabled': 1}


class Matching(unittest.TestCase):
    def apply(self, text, *rules):
        return aliases.apply(text, list(rules))

    def test_exact_whole_word_with_original_kept(self):
        result = self.apply('عايز أعمل ريدزاين للصفحة', rule(1, 'ريدزاين', 'redesign'))
        self.assertEqual(result['text'], 'عايز أعمل redesign للصفحة')
        self.assertEqual(result['original'], 'عايز أعمل ريدزاين للصفحة')
        self.assertEqual(result['replacements'][0]['heard'], 'ريدزاين')
        self.assertEqual(result['replacements'][0]['ruleId'], 1)

    def test_arabic_prefixes_stay_in_front_of_the_english_term(self):
        r = rule(1, 'ريدزاين', 'redesign')
        self.assertEqual(self.apply('الريدزاين جاهز', r)['text'], 'الـ redesign جاهز')
        self.assertEqual(self.apply('الـريدزاين جاهز', r)['text'], 'الـ redesign جاهز')
        self.assertEqual(self.apply('والريدزاين', r)['text'], 'والـ redesign')
        self.assertEqual(self.apply('للريدزاين', r)['text'], 'للـ redesign')
        self.assertEqual(self.apply('وريدزاين', r)['text'], 'و redesign')
        self.assertEqual(self.apply('تريدزاين', r)['text'], 'ت redesign')

    def test_no_match_inside_other_words_or_with_suffixes(self):
        r = rule(1, 'تست', 'test')
        for text in ('تستات كتير', 'متستش', 'اختست'):
            self.assertEqual(self.apply(text, r)['text'], text)
        self.assertEqual(self.apply('تست، تست.', r)['text'], 'test، test.')

    def test_orthographic_variants_and_diacritics_match(self):
        r = rule(1, 'أبلكيشن', 'application')
        self.assertEqual(self.apply('الابلكيشن', r)['text'], 'الـ application')
        self.assertEqual(self.apply('أَبْلِكيشن', r)['text'], 'application')

    def test_longest_phrase_first_and_multiword_spacing(self):
        result = self.apply('اعمل لوجين فلو جديد ولوجين بس',
                            rule(1, 'لوجين', 'login'), rule(2, 'لوجين فلو', 'login flow'))
        self.assertEqual(result['text'], 'اعمل login flow جديد و login بس')
        self.assertEqual([r['ruleId'] for r in result['replacements']], [2, 1])
        self.assertEqual(self.apply('لوجين   فلو', rule(2, 'لوجين فلو', 'login flow'))['text'], 'login flow')

    def test_crossing_rules_conflict_and_change_nothing(self):
        result = self.apply('داتا بيز سيرفر', rule(1, 'داتا بيز', 'database'), rule(2, 'بيز سيرفر', 'base server'))
        self.assertEqual(result['text'], 'داتا بيز سيرفر')
        self.assertEqual(result['conflicts'][0]['rules'], [1, 2])

    def test_english_identifiers_paths_numbers_and_quotes_are_protected(self):
        r = rule(1, 'ريدزاين', 'redesign')
        for text in ('"ريدزاين"', '«ريدزاين»', '`ريدزاين`', 'src/ريدزاين/index.ts'):
            self.assertEqual(self.apply(text, r)['text'], text, text)
        text = 'Keep React 18 and parseConfig() as they are, then ريدزاين'
        self.assertEqual(self.apply(text, r)['text'], 'Keep React 18 and parseConfig() as they are, then redesign')

    def test_offsets_describe_both_texts(self):
        result = self.apply('ابدأ الريدزاين الآن', rule(1, 'ريدزاين', 'redesign'))
        item = result['replacements'][0]
        self.assertEqual(result['original'][item['start']:item['end']], 'الريدزاين')
        self.assertEqual(result['text'][item['outputStart']:item['outputEnd']], 'الـ redesign')


class Storage(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        patcher = patch.object(paths, 'DATA', Path(temp.name))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_migration_keeps_learned_words_corrections_and_preference(self):
        # A version-1 learned database, as shipped before aliases existed.
        db = sqlite3.connect(paths.DATA / 'vocabulary.sqlite3')
        db.execute('CREATE TABLE vocabulary (word TEXT PRIMARY KEY, uses INTEGER NOT NULL, updated REAL NOT NULL)')
        db.execute('CREATE TABLE corrections (heard TEXT PRIMARY KEY, spelling TEXT NOT NULL, updated REAL NOT NULL)')
        db.execute("INSERT INTO vocabulary VALUES ('React', 3, 1)")
        db.execute("INSERT INTO corrections VALUES ('ريدزاين', 'redesign', 1)")
        db.commit()
        db.close()
        store.set_self_learning(False)
        self.assertEqual(store.personal_vocabulary(), {'words': ['React'], 'corrections': [{'heard': 'ريدزاين', 'spelling': 'redesign'}]})
        self.assertFalse(store.self_learning_enabled())
        listing = aliases.listing()
        self.assertEqual(listing['aliases'], [])
        self.assertEqual(listing['proposals'], [{'heard': 'ريدزاين', 'spelling': 'redesign'}])  # proposed, not applied
        self.assertEqual(aliases.apply('ريدزاين')['text'], 'ريدزاين')
        with store.database(vocabulary=True) as db:
            self.assertEqual(db.execute('PRAGMA learned.user_version').fetchone()[0], 2)

    def test_approval_creates_a_rule_and_removes_the_proposal(self):
        store.learn_vocabulary('عايز أعمل redesign', 'عايز أعمل ريدزاين')
        listing = aliases.add('ريدزاين', 'redesign', provenance='learned')
        self.assertEqual([(a['heard'], a['canonical'], a['provenance'], a['enabled']) for a in listing['aliases']],
                         [('ريدزاين', 'redesign', 'learned', 1)])
        self.assertEqual(listing['proposals'], [])
        self.assertEqual(aliases.apply('ريدزاين')['text'], 'redesign')

    def test_edit_disable_delete_and_duplicates(self):
        rule_id = aliases.add('دارك مود', 'dark mode')['aliases'][0]['id']
        with self.assertRaisesRegex(ValueError, 'ليها قاعدة بالفعل'):
            aliases.add('دارك  مود', 'Dark Mode')
        aliases.update(rule_id, canonical='Dark Mode')
        self.assertEqual(aliases.apply('ضيف دارك مود')['text'], 'ضيف Dark Mode')
        aliases.update(rule_id, enabled=False)
        self.assertEqual(aliases.apply('ضيف دارك مود')['text'], 'ضيف دارك مود')
        aliases.delete(rule_id)
        self.assertEqual(aliases.listing()['aliases'], [])
        with self.assertRaisesRegex(ValueError, 'مش موجودة'):
            aliases.update(rule_id, enabled=True)

    def test_validation_rejects_unsafe_rules(self):
        for heard, canonical in (('redesign', 'redesign'), ('ر', 'r'), ('ريدزاين', 'ريدزاين'), ('ريدزاين', ''),
                                 ('ريدزاين', 'x' * 81), ('ريدزاين1', 'redesign'), ('ريدزاين', 'rm -rf; echo')):
            with self.assertRaises(ValueError, msg=(heard, canonical)):
                aliases.add(heard, canonical)

    def test_forgetting_learned_vocabulary_keeps_approved_rules(self):
        aliases.add('ريدزاين', 'redesign')
        store.forget_vocabulary()
        self.assertEqual(len(aliases.listing()['aliases']), 1)


if __name__ == '__main__':
    unittest.main()
