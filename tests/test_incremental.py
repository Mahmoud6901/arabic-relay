"""Processing during speech: window planning, seam reconciliation, job ownership and whole-recording fallback."""
import struct
import threading
import time
import unittest

import numpy as np

from arabic_relay import incremental
from arabic_relay.incremental import RATE, Ambiguous

HALF = int(incremental.HALF_OVERLAP * RATE)


def window(index, start_s, end_s, cut_s, words):
    """`words` are (text, start, end) on the recording timeline; stored window-local like the worker returns them."""
    start = int(start_s * RATE)
    return {'index': index, 'start': start, 'end': int(end_s * RATE), 'cut': None if cut_s is None else int(cut_s * RATE), 'half': HALF,
            'result': {'segments': [{'text': ''.join(' ' + w for w, _, _ in words), 'avg_logprob': -0.2,
                                     'no_speech_prob': 0.01, 'compression_ratio': 1.2,
                                     'words': [{'start_s': a - start_s, 'end_s': b - start_s, 'text': ' ' + w,
                                                'probability': 0.9} for w, a, b in words]}]}}


def text(windows):
    return incremental.assemble(incremental.reconcile(windows))[0]


class Seams(unittest.TestCase):
    def test_clean_pause_keeps_every_word_once(self):
        first = window(0, 0, 11, 10, [('عايز', 1, 1.5), ('أعمل', 1.6, 2), ('redesign', 9.0, 9.6), ('للصفحة', 10.4, 10.9)])
        second = window(1, 9, 20, None, [('redesign', 9.05, 9.6), ('للصفحة', 10.4, 10.95), ('الرئيسية', 11.0, 11.6)])
        self.assertEqual(text([first, second]), 'عايز أعمل redesign للصفحة الرئيسية')

    def test_repeated_words_across_the_seam_are_not_collapsed_or_doubled(self):
        words = [('test', 9.0, 9.3), ('test', 9.4, 9.7), ('test', 10.3, 10.6), ('test', 10.7, 11.0)]
        first = window(0, 0, 11, 10, [('run', 1, 1.4)] + words[:3] + [('tes', 10.7, 10.99)])
        second = window(1, 9, 20, None, words + [('done', 12, 12.5)])
        self.assertEqual(text([first, second]), 'run test test test test done')

    def test_word_spanning_the_cut_seen_by_both_windows_is_kept_once(self):
        first = window(0, 0, 11, 10, [('ما', 9.2, 9.5), ('تغيرش', 9.7, 10.3)])
        second = window(1, 9, 20, None, [('ما', 9.25, 9.5), ('تغيرش', 9.75, 10.3), ('login', 10.5, 11)])
        self.assertEqual(text([first, second]), 'ما تغيرش login')

    def test_unmatched_word_at_the_cut_is_ambiguous(self):
        first = window(0, 0, 11, 10, [('خمسة', 9.85, 10.1)])
        second = window(1, 9, 20, None, [('ستة', 9.9, 10.15)])
        with self.assertRaises(Ambiguous):
            incremental.reconcile([first, second])

    def test_spelling_variants_of_one_moment_keep_the_better_context_version_once(self):
        # Measured with large-v3: two windows heard the same words at the same time, spelled differently.
        first = window(0, 0, 11, 10, [('تاني', 9.0, 9.3), ('شوفها', 9.6, 10.1), ('اللوكز', 10.1, 10.9)])
        second = window(1, 9, 20, None, [('تاني', 9.05, 9.3), ('شوف', 9.4, 9.9), ('اللوكس', 9.9, 10.9), ('كويس', 10.9, 11.6)])
        self.assertIn(text([first, second]), ('تاني شوفها اللوكز كويس', 'تاني شوف اللوكس كويس'))

    def test_a_word_split_differently_is_one_group(self):
        first = window(0, 0, 11, 10, [('index.html', 9.6, 10.4)])
        second = window(1, 9, 20, None, [('index', 9.6, 9.9), ('.html', 9.9, 10.4), ('ok', 11, 11.5)])
        self.assertEqual(text([first, second]).replace(' ', ''), 'index.htmlok')

    def test_different_numbers_or_negation_at_the_cut_are_never_guessed(self):
        for left, right in ((('15', 9.7, 10.2),), (('16', 9.7, 10.2),)), ((('متغيرش', 9.6, 10.3),), (('تغير', 9.7, 10.3),)):
            with self.assertRaises(Ambiguous, msg=(left, right)):
                incremental.reconcile([window(0, 0, 11, 10, list(left)), window(1, 9, 20, None, list(right))])

    def test_each_window_is_trusted_on_its_own_side(self):
        # The earlier window's garbled tail and the later window's garbled head are both discarded.
        first = window(0, 0, 11, 10, [('app', 9.0, 9.4), ('setti', 10.6, 10.99)])
        second = window(1, 9, 20, None, [('ap', 9.0, 9.2), ('settings', 10.5, 11.1)])
        self.assertEqual(text([first, second]), 'app settings')

    def test_arabic_prefix_attached_to_english_matches_across_windows(self):
        first = window(0, 0, 11, 10, [('الـapplication', 9.6, 10.4)])
        second = window(1, 9, 20, None, [('الapplication', 9.6, 10.4)])
        self.assertEqual(incremental.normalize('الـapplication'), incremental.normalize('الapplication'))
        self.assertEqual(text([first, second]).count('application'), 1)

    def test_numbers_and_negation_survive(self):
        first = window(0, 0, 11, 10, [('مش', 2, 2.3), ('عايز', 2.4, 2.8), ('3', 9.0, 9.2), ('tests', 9.3, 9.8)])
        second = window(1, 9, 20, None, [('3', 9.0, 9.2), ('tests', 9.3, 9.8), ('بس', 10.4, 10.8)])
        self.assertEqual(text([first, second]), 'مش عايز 3 tests بس')

    def test_missing_coverage_and_bad_timestamps_fall_back(self):
        first = window(0, 0, 11, 10, [('a', 1, 2)])
        gap = window(1, 9.5, 20, None, [('b', 12, 13)])
        with self.assertRaises(Ambiguous):
            incremental.reconcile([first, gap])
        late = window(0, 0, 11, None, [('a', 5, 4)])
        with self.assertRaises(Ambiguous):
            incremental.reconcile([late])
        outside = window(0, 0, 11, None, [('a', 1, 14)])
        with self.assertRaises(Ambiguous):
            incremental.reconcile([outside])
        with self.assertRaises(Ambiguous):
            incremental.reconcile([window(0, 1, 11, None, [])])

    def test_assembled_segments_are_on_the_recording_timeline(self):
        first = window(0, 0, 11, 10, [('one', 1, 1.5)])
        second = window(1, 9, 20, None, [('two', 15, 15.5)])
        _, segments = incremental.assemble(incremental.reconcile([first, second]))
        self.assertEqual([(s['start_s'], s['words'][0]['start_s']) for s in segments], [(1, 1), (15, 15)])
        self.assertEqual(set(segments[0]), {'start_s', 'end_s', 'text', 'avg_logprob', 'no_speech_prob', 'compression_ratio', 'words'})


class Windows(unittest.TestCase):
    def read(self, samples):
        return lambda low, high: samples[low:high]

    def test_a_real_pause_keeps_both_window_edges_inside_the_silence(self):
        samples = (np.ones(14 * RATE) * 3000).astype(np.int16)
        samples[int(10.0 * RATE):int(10.6 * RATE)] = 0
        start, end, cut, half = incremental.plan_cut(self.read(samples), 0, len(samples))
        self.assertTrue(10.2 * RATE <= cut <= 10.4 * RATE)
        self.assertEqual(end, cut + half)
        self.assertGreaterEqual(cut - half, 10.0 * RATE)
        self.assertLessEqual(cut + half, 10.6 * RATE)

    def test_without_a_pause_the_quietest_frame_gets_a_two_second_overlap(self):
        samples = (np.ones(14 * RATE) * 3000).astype(np.int16)
        samples[int(10.2 * RATE):int(10.4 * RATE)] = 500   # a dip shorter than a pause
        start, end, cut, half = incremental.plan_cut(self.read(samples), 0, len(samples))
        self.assertTrue(10.2 * RATE <= cut <= 10.4 * RATE)
        self.assertEqual(half, HALF)

    def test_quiet_speech_is_judged_relative_to_its_own_level(self):
        samples = (np.ones(14 * RATE) * 300).astype(np.int16)  # a quiet speaker, not silence
        samples[int(9.0 * RATE):int(9.5 * RATE)] = 0
        _, _, cut, half = incremental.plan_cut(self.read(samples), 0, len(samples))
        self.assertTrue(9.0 * RATE <= cut - half and cut + half <= 9.5 * RATE)

    def test_needs_enough_audio_and_finishes_short_recordings_whole(self):
        samples = np.zeros(5 * RATE, dtype=np.int16)
        self.assertIsNone(incremental.plan_cut(self.read(samples), 0, len(samples)))
        self.assertEqual(incremental.plan_cut(self.read(samples), 0, len(samples), final=True), (0, len(samples), None, 0))
        self.assertIsNone(incremental.plan_cut(self.read(samples), len(samples), len(samples), final=True))

    def test_every_window_respects_the_cap(self):
        samples = (np.random.default_rng(1).standard_normal(60 * RATE) * 2000).astype(np.int16)
        start, spans = 0, []
        while True:
            plan = incremental.plan_cut(self.read(samples), start, len(samples), final=True)
            if plan is None:
                break
            spans.append(plan)
            if plan[2] is None:
                break
            start = plan[2] - plan[3]
        self.assertEqual(spans[-1][1], len(samples))
        self.assertTrue(all(end - begin <= incremental.WINDOW_CAP * RATE for begin, end, _, _ in spans))
        for (_, end, cut, _), (begin, _, _, _) in zip(spans, spans[1:]):
            self.assertLess(begin, end)  # overlapping: no sample between windows is skipped


SCRIPT = [('سلام', 0.5), ('عايز', 2.0), ('redesign', 4.0), ('للصفحة', 6.0), ('الرئيسية', 8.0), ('ومش', 9.6),
          ('عايز', 11.2), ('أغير', 13.0), ('login', 15.0), ('flow', 17.0), ('3', 19.0), ('مرات', 21.0), ('خالص', 23.5)]


def scripted_audio(seconds=25):
    # A faint random floor keeps every slice unique, so the fake recognizer can locate its window.
    samples = np.random.default_rng(11).integers(-3, 4, seconds * RATE).astype(np.int16)
    for _, at in SCRIPT:
        samples[int(at * RATE):int((at + 0.5) * RATE)] = 4000
    return samples.tobytes()


class FakeRecognizer:
    """Hears the scripted words inside each window; a word cut by the window edge comes back garbled."""

    def __init__(self, audio, fail_on=None, delay=0):
        self.audio, self.calls, self.fail_on, self.delay = audio, [], fail_on, delay

    def __call__(self, header, payload, cancel_event):
        self.calls.append(header)
        time.sleep(self.delay)
        if self.fail_on is not None and len(self.calls) == self.fail_on:
            raise RuntimeError('decode failed')
        offset = self.audio.find(payload) / 2 / RATE
        duration = len(payload) / 2 / RATE
        words = []
        for text, at in SCRIPT:
            start, end = at - offset, at + 0.5 - offset
            if end <= 0 or start >= duration:
                continue
            clipped = start < 0 or end > duration
            words.append({'start_s': max(start, 0), 'end_s': min(end, duration), 'text': ' ' + (text[:2] if clipped else text),
                          'probability': 0.9})
        return {'segments': [{'text': ''.join(w['text'] for w in words), 'avg_logprob': -0.3, 'no_speech_prob': 0.0,
                              'compression_ratio': 1.1, 'words': words}], 'device': 'GPU', 'fallback': False,
                'effectiveAsrConfig': {'path': 'window'}}


def feed(job, audio, chunk=RATE * 2):
    seq = 0
    for start in range(0, len(audio), chunk):
        job.append(seq, audio[start:start + chunk])
        seq += 1
    return seq - 1


class Jobs(unittest.TestCase):
    def test_windows_during_recording_join_to_the_spoken_words(self):
        audio = scripted_audio()
        recognizer = FakeRecognizer(audio)
        job = incremental.Job('job-' + 'a' * 20, False, ['React'], ('model', 'cuda'), recognizer)
        last = feed(job, audio)
        result, reason = job.finish(len(audio) // 2, last, timeout=10)
        self.assertIsNone(reason)
        self.assertEqual(result['text'].split(), [word for word, _ in SCRIPT])
        self.assertGreaterEqual(len(job.windows), 2)
        self.assertTrue(all(call['window'] and call['keywords'] == ['React'] for call in recognizer.calls))
        self.assertEqual(result['effectiveAsrConfig']['path'], 'incremental')

    def test_a_short_utterance_is_one_whole_window(self):
        audio = scripted_audio(6)[:6 * RATE * 2]
        recognizer = FakeRecognizer(audio)
        job = incremental.Job('job-' + 'i' * 20, False, [], ('model', 'cuda'), recognizer)
        last = feed(job, audio)
        result, reason = job.finish(len(audio) // 2, last, timeout=10)
        self.assertIsNone(reason)
        self.assertEqual(len(job.windows), 1)
        self.assertIsNone(job.windows[0]['cut'])
        self.assertTrue(result['text'].startswith('سلام'))
        # Nothing to join: Craft decodes it like a whole recording, without word timestamps.
        self.assertEqual(recognizer.calls[0]['window'], False)
        self.assertTrue(result['effectiveAsrConfig']['wholeClip'])
        whole = incremental.Job('job-' + 'w' * 20, False, [], ('model', 'cuda'),
                                lambda header, payload, cancel: {'text': ' سلام عايز ', 'device': 'GPU'})
        self.assertEqual(whole.finish(len(audio) // 2, feed(whole, audio), timeout=10)[0]['text'], 'سلام عايز')
        # Inline needs word times for its spans, so it keeps the window decode.
        recognizer = FakeRecognizer(audio)
        job = incremental.Job('job-' + 'k' * 20, True, [], ('model', 'cuda'), recognizer)
        result, reason = job.finish(len(audio) // 2, feed(job, audio), timeout=10)
        self.assertIsNone(reason)
        self.assertEqual(recognizer.calls[0]['window'], True)
        self.assertTrue(result['segments'][0]['words'])

    def test_silence_produces_no_transcript(self):
        audio = bytes(2 * 8 * RATE)
        job = incremental.Job('job-' + 'j' * 20, False, [], ('model', 'cuda'),
                              lambda header, payload, cancel: {'segments': []})
        last = feed(job, audio)
        result, reason = job.finish(len(audio) // 2, last, timeout=10)
        self.assertIsNone(reason)
        self.assertEqual(result['text'], '')

    def test_out_of_order_or_duplicate_chunks_are_rejected(self):
        job = incremental.Job('job-' + 'b' * 20, False, [], ('model', 'cpu'), FakeRecognizer(b''))
        job.append(0, b'\0\0' * 100)
        for seq in (0, 2):
            with self.assertRaisesRegex(ValueError, 'out of order'):
                job.append(seq, b'\0\0' * 100)
        with self.assertRaises(ValueError):
            job.append(1, b'\0' * 3)  # odd byte count
        with self.assertRaises(ValueError):
            job.append(1, b'\0\0' * (incremental.MAX_CHUNK + 1))
        job.cancel()

    def test_a_failed_window_stops_partials_and_requests_the_whole_recording(self):
        audio = scripted_audio()
        job = incremental.Job('job-' + 'c' * 20, False, [], ('model', 'cuda'), FakeRecognizer(audio, fail_on=1))
        last = feed(job, audio)
        result, reason = job.finish(len(audio) // 2, last, timeout=10)
        self.assertIsNone(result)
        self.assertTrue(reason.startswith('failed'))

    def test_missing_audio_at_stop_requests_the_whole_recording(self):
        audio = scripted_audio()
        job = incremental.Job('job-' + 'd' * 20, False, [], ('model', 'cuda'), FakeRecognizer(audio))
        last = feed(job, audio)
        result, reason = job.finish(len(audio) // 2 + 1, last, timeout=10)
        self.assertIsNone(result)
        self.assertEqual(reason, 'missing audio')

    def test_backlog_stops_partial_inference_instead_of_queueing(self):
        audio = scripted_audio(60)
        recognizer = FakeRecognizer(audio, delay=1.5)
        job = incremental.Job('job-' + 'e' * 20, False, [], ('model', 'cpu'), recognizer)
        last = feed(job, audio)          # 60 s arrive at once: far beyond the 30 s backlog limit
        result, reason = job.finish(len(audio) // 2, last, timeout=10)
        self.assertIsNone(result)
        self.assertEqual(reason, 'backlog')
        self.assertLessEqual(len(recognizer.calls), 2)

    def test_slower_than_real_time_inference_stops_partials_early(self):
        audio = scripted_audio(26)
        inner = FakeRecognizer(audio)
        def slow(header, payload, cancel_event):
            time.sleep(len(payload) / 2 / RATE * 1.15)   # decode cannot keep up with the microphone
            return inner(header, payload, cancel_event)
        job = incremental.Job('job-' + 'h' * 20, False, [], ('model', 'cpu'), slow)
        last = feed(job, audio)
        for _ in range(300):                             # let the first window finish while recording continues
            if job.stopped_reason:
                break
            time.sleep(.1)
        result, reason = job.finish(len(audio) // 2, last, timeout=30)
        self.assertIsNone(result)
        self.assertEqual(reason, 'behind')
        self.assertEqual(len(job.windows), 1)            # one window, then the authoritative whole recording

    def test_cancel_discards_audio_and_late_results(self):
        audio = scripted_audio()
        gate = threading.Event()
        def slow(header, payload, cancel_event):
            gate.wait(5)
            return FakeRecognizer(audio)(header, payload, cancel_event)
        job = incremental.Job('job-' + 'f' * 20, True, [], ('model', 'cuda'), slow)
        feed(job, audio)
        time.sleep(.2)
        job.cancel()
        gate.set()
        job.thread.join(5)
        self.assertEqual((job.windows, bytes(job.pcm), job.provisional), ([], b'', ''))
        with self.assertRaises(ValueError):
            job.append(99, b'\0\0')

    def test_five_minute_limit_is_enforced_on_the_server(self):
        job = incremental.Job('job-' + 'g' * 20, False, [], ('model', 'cuda'), lambda *a: {'segments': []})
        job.pcm = bytearray(incremental.MAX_SAMPLES * 2)
        job.next_seq = 5
        with self.assertRaisesRegex(ValueError, 'five minutes'):
            job.append(5, b'\0\0')
        job.cancel()


class Registry(unittest.TestCase):
    def test_one_job_at_a_time_and_only_its_id_reaches_it(self):
        released = []
        registry = incremental.Registry(on_release=released.append)
        first = registry.start('first-' + 'x' * 20, lambda: incremental.Job('first-' + 'x' * 20, False, [], ('m', 'cpu'), None))
        self.assertIsNone(registry.start('second-' + 'y' * 20, lambda: self.fail('second job created')))
        with self.assertRaises(ValueError):
            registry.get('second-' + 'y' * 20)
        self.assertIs(registry.get('first-' + 'x' * 20), first)
        self.assertTrue(registry.release(first))
        self.assertFalse(registry.release(first))
        self.assertEqual(released, [first])
        with self.assertRaises(ValueError):
            registry.start('bad id', lambda: None)

    def test_abandoned_recordings_expire_and_release_the_lock(self):
        released = []
        registry = incremental.Registry(on_release=released.append)
        job = registry.start('idle-' + 'z' * 20, lambda: incremental.Job('idle-' + 'z' * 20, False, [], ('m', 'cpu'), None))
        job.touched -= incremental.IDLE_EXPIRY + 1
        registry.expire()
        self.assertEqual(released, [job])
        self.assertTrue(job.cancelled.is_set())


if __name__ == '__main__':
    unittest.main()
