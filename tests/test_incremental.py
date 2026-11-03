"""Processing during speech: confirmed words across passes, buffer trimming, the final reading at Stop, job
ownership and whole-recording fallback."""
import threading
import time
import unittest

import numpy as np

from arabic_relay import incremental
from arabic_relay.incremental import RATE, Agreement, Ambiguous


def word(text, start, end, window=0, segment=0):
    return {'text': ' ' + text, 'start_s': start, 'end_s': end, 'probability': .9, 'window': window, 'segment': segment,
            'metrics': {}}


def words(*items, window=0):
    return [word(text, start, end, window) for text, start, end in items]


class Agreements(unittest.TestCase):
    def test_a_word_is_confirmed_only_when_two_passes_read_it_alike(self):
        agreement = Agreement()
        self.assertEqual(agreement.update(words(('عايز', .1, .5), ('re', .9, 1.0))), [])
        confirmed = agreement.update(words(('عايز', .1, .5), ('redesign', .9, 1.4), window=1))
        self.assertEqual([w['text'] for w in confirmed], [' عايز'])          # the word cut at the edge waits
        self.assertEqual([w['text'] for w in agreement.tentative], [' redesign'])
        agreement.update(words(('عايز', .1, .5), ('redesign', .9, 1.4), ('للصفحة', 1.6, 2.0), window=2))
        self.assertEqual([w['text'] for w in agreement.committed], [' عايز', ' redesign'])

    def test_numbers_and_negation_are_never_confirmed_on_a_disagreement(self):
        agreement = Agreement()
        agreement.update(words(('مش', .1, .3), ('3', .5, .7)))
        agreement.update(words(('مش', .1, .3), ('4', .5, .7), window=1))
        self.assertEqual([w['text'] for w in agreement.committed], [' مش'])
        agreement.update(words(('مش', .1, .3), ('3', .5, .7), window=2))
        self.assertEqual([w['text'] for w in agreement.committed], [' مش'])   # 4 then 3: still not agreed

    def test_confirmed_words_heard_again_are_not_doubled_and_real_repeats_are_kept(self):
        agreement = Agreement()
        agreement.update(words(('عايز', .1, .5), ('عايز', .7, 1.1)))
        agreement.update(words(('عايز', .1, .5), ('عايز', .7, 1.1), ('ده', 1.3, 1.5), window=1))
        self.assertEqual(len(agreement.committed), 2)                         # a spoken repetition stays twice
        # A later pass over the same (untrimmed) audio hears the confirmed words again, slightly shifted in time.
        fresh = agreement.fresh(words(('عايز', .15, .5), ('عايز', .75, 1.15), ('ده', 1.3, 1.5), window=2))
        self.assertEqual([w['text'] for w in fresh], [' ده'])

    def test_a_trim_happens_only_in_a_pause_before_the_recent_audio(self):
        committed = words(('سلام', .5, 1.0), ('عايز', 2.0, 2.5), ('redesign', 4.0, 4.5), ('للصفحة', 6.0, 6.5))
        self.assertIsNone(incremental.trim_point(committed, [], 0, 9.0))     # a short buffer is kept whole
        cut = incremental.trim_point(committed, words(('الرئيسية', 8.0, 8.5)), 0, 11.0)
        self.assertTrue(4.5 < cut < 6.0, cut)                                # the latest pause before the kept audio
        self.assertLessEqual(cut, 11.0 - incremental.KEEP)                   # the latest seconds stay in the buffer
        touching = words(('a', 0, 1), ('b', 1, 2), ('c', 2, 3), ('d', 3, 4), ('e', 4, 5), ('f', 5, 6))
        self.assertIsNone(incremental.trim_point(touching, [], 0, 12.0))     # no pause, buffer still short enough
        self.assertEqual(incremental.trim_point(touching, [], 0, 21.0), 6)   # a long buffer cuts after a confirmed word

    def test_the_tail_pass_after_stop_starts_in_the_pause_before_the_unconfirmed_words(self):
        committed = words(('سلام', .5, 1.0), ('عايز', 2.0, 2.5), ('redesign', 4.0, 4.5))
        tentative = words(('للصفحة', 6.0, 6.5))
        self.assertEqual(incremental.tail_cut(committed, tentative, 0), 5.25)
        self.assertEqual(incremental.tail_cut(committed, [], 0), 3.25)            # the latest pause among confirmed words
        touching = words(('a', 0, 1), ('b', 1, 2), ('c', 2, 3))
        self.assertEqual(incremental.tail_cut(touching, words(('d', 3, 4)), 0), 2)  # no pause: hear the last word again
        self.assertEqual(incremental.tail_cut([], tentative, 1.5), 1.5)           # nothing confirmed: the whole buffer
        self.assertEqual(incremental.tail_cut(committed, tentative, 4.8), 4.8)    # never before the buffer

    def test_silence_is_judged_against_the_speech_level(self):
        level = incremental.speech_level(np.full(RATE, 3000, dtype=np.int16))
        self.assertEqual(round(level), 3000)
        self.assertTrue(incremental.silent(np.full(RATE // 2, 40, dtype=np.int16), level))
        self.assertFalse(incremental.silent(np.full(RATE // 2, 1500, dtype=np.int16), level))
        tail = np.full(RATE // 2, 40, dtype=np.int16)
        tail[4000:4400] = 2000                                               # 25 ms of a word is speech
        self.assertFalse(incremental.silent(tail, level))
        self.assertTrue(incremental.silent(np.zeros(0, dtype=np.int16), level))
        self.assertFalse(incremental.silent(np.full(RATE // 2, 200, dtype=np.int16), 0.0))  # no speech heard yet

    def test_bad_timestamps_are_ambiguous(self):
        segment = {'text': ' x', 'words': [{'start_s': 1.0, 'end_s': .5, 'text': ' x'}]}
        with self.assertRaises(Ambiguous):
            incremental.window_words({'index': 0, 'start': 0, 'end': RATE * 2, 'result': {'segments': [segment]}})
        segment = {'text': ' x', 'words': []}
        with self.assertRaises(Ambiguous):
            incremental.window_words({'index': 0, 'start': 0, 'end': RATE * 2, 'result': {'segments': [segment]}})

    def test_assembled_segments_are_on_the_recording_timeline(self):
        text, segments = incremental.assemble(words(('عايز', 3.0, 3.4), ('redesign', 3.5, 4.0)))
        self.assertEqual(text, 'عايز redesign')
        self.assertEqual((segments[0]['start_s'], segments[0]['end_s']), (3.0, 4.0))


SCRIPT = [('سلام', 0.5), ('عايز', 2.0), ('redesign', 4.0), ('للصفحة', 6.0), ('الرئيسية', 8.0), ('ومش', 9.6),
          ('عايز', 11.2), ('أغير', 13.0), ('login', 15.0), ('flow', 17.0), ('3', 19.0), ('مرات', 21.0), ('خالص', 23.5)]


def scripted_audio(seconds=25):
    # A faint random floor keeps every slice unique, so the fake recognizer can locate its window.
    samples = np.random.default_rng(11).integers(-3, 4, seconds * RATE).astype(np.int16)
    for _, at in SCRIPT:
        samples[int(at * RATE):int((at + 0.5) * RATE)] = 4000
    return samples.tobytes()


class FakeRecognizer:
    """Hears the scripted words inside each pass; a word cut by the pass's edge comes back garbled."""

    def __init__(self, audio, fail_on=None, delay=0):
        self.audio, self.calls, self.fail_on, self.delay = audio, [], fail_on, delay

    def __call__(self, header, payload, cancel_event):
        self.calls.append(header)
        time.sleep(self.delay)
        if self.fail_on is not None and len(self.calls) == self.fail_on:
            raise RuntimeError('decode failed')
        offset = self.audio.find(payload) / 2 / RATE
        duration = len(payload) / 2 / RATE
        found = []
        for text, at in SCRIPT:
            start, end = at - offset, at + 0.5 - offset
            if end <= 0 or start >= duration:
                continue
            clipped = start < 0 or end > duration
            found.append({'start_s': max(start, 0), 'end_s': min(end, duration), 'text': ' ' + (text[:2] if clipped else text),
                          'probability': 0.9})
        return {'segments': [{'text': ''.join(w['text'] for w in found), 'avg_logprob': -0.3, 'no_speech_prob': 0.0,
                              'compression_ratio': 1.1, 'words': found}], 'device': 'GPU', 'fallback': False,
                'effectiveAsrConfig': {'path': 'window', 'detectedLanguage': header.get('language') or 'ar'}}


def feed(job, audio, chunk=RATE // 2, pause=0.0):
    """Chunks of 250 ms; with `pause`, passes run while the recording is still arriving."""
    seq = 0
    for start in range(0, len(audio), chunk):
        job.append(seq, audio[start:start + chunk])
        seq += 1
        if pause:
            time.sleep(pause)
    return seq - 1


def spoken():
    return [text for text, _ in SCRIPT]


class Jobs(unittest.TestCase):
    def test_passes_while_recording_confirm_the_spoken_words_and_stop_needs_no_decode(self):
        audio = scripted_audio()
        recognizer = FakeRecognizer(audio)
        job = incremental.Job('job-' + 'a' * 20, False, ['React'], ('model', 'cuda'), recognizer)
        last = feed(job, audio, pause=.01)
        for _ in range(100):                          # the last pass catches up with the end of the recording
            if job.decoded_to == len(audio) // 2:
                break
            time.sleep(.02)
        calls = len(recognizer.calls)
        result, reason = job.finish(len(audio) // 2, last, timeout=10)
        self.assertIsNone(reason)
        self.assertEqual(result['text'].split(), spoken())
        self.assertGreater(calls, 5)
        self.assertEqual(len(recognizer.calls), calls, 'trailing silence: the latest pass was already final')
        self.assertEqual(result['effectiveAsrConfig']['finalDecode'], 'none')
        self.assertTrue(all(call['window'] and call['keywords'] == ['React'] for call in recognizer.calls))
        # Confirmed audio was trimmed away, and later passes received the confirmed text as their prompt.
        self.assertGreater(job.buffer_start, 0)
        self.assertTrue(any(call['context'] for call in recognizer.calls))
        self.assertTrue(job.provisional)
        # The first pass that heard words detected the language; every later pass reuses it.
        first_heard = next(i for i, call in enumerate(recognizer.calls) if call.get('language'))
        self.assertTrue(all(call.get('language') == 'ar' for call in recognizer.calls[first_heard:]))
        self.assertNotIn('language', recognizer.calls[0])

    def test_no_pass_runs_over_silence_after_the_last_word(self):
        audio = scripted_audio(9) + bytes(2 * 6 * RATE)          # the last word ends at 8.5 s, then silence
        recognizer = FakeRecognizer(audio)
        job = incremental.Job('job-' + 's' * 20, False, [], ('model', 'cuda'), recognizer)
        seq = feed(job, audio[:2 * 11 * RATE], pause=.01)
        for _ in range(100):
            if job.final_ready(len(job.pcm) // 2) and not job.due(len(job.pcm) // 2):
                break
            time.sleep(.02)
        calls = len(recognizer.calls)
        for start in range(2 * 11 * RATE, len(audio), RATE // 2):  # four more seconds of silence arrive
            seq += 1
            job.append(seq, audio[start:start + RATE // 2])
        time.sleep(.3)
        self.assertEqual(len(recognizer.calls), calls, 'the GPU stays idle while the user is silent')
        result, reason = job.finish(len(audio) // 2, seq, timeout=10)
        self.assertIsNone(reason)
        self.assertEqual(result['effectiveAsrConfig']['finalDecode'], 'none')
        self.assertEqual(len(recognizer.calls), calls)
        self.assertEqual(result['text'].split(), ['سلام', 'عايز', 'redesign', 'للصفحة', 'الرئيسية'])

    def test_speech_still_arriving_at_stop_gets_one_tail_pass(self):
        audio = scripted_audio(9)
        gate, started = threading.Event(), threading.Event()
        inner = FakeRecognizer(audio)

        def gated(header, payload, cancel_event):
            started.set()
            gate.wait(5)
            return inner(header, payload, cancel_event)
        job = incremental.Job('job-' + 't' * 20, False, [], ('model', 'cuda'), gated)
        job.append(0, audio[:2 * RATE * 2])
        job.append(1, audio[2 * RATE * 2:3 * RATE * 2])
        self.assertTrue(started.wait(5))              # the first pass covers only the first seconds
        seq = 2
        for start in range(3 * RATE * 2, len(audio), RATE):
            job.append(seq, audio[start:start + RATE])
            seq += 1
        outcome = {}
        finisher = threading.Thread(target=lambda: outcome.update(value=job.finish(len(audio) // 2, seq - 1, timeout=10)))
        finisher.start()
        gate.set()
        finisher.join(10)
        result, reason = outcome['value']
        self.assertIsNone(reason)
        self.assertEqual(result['text'].split(), ['سلام', 'عايز', 'redesign', 'للصفحة', 'الرئيسية'])
        self.assertEqual(result['effectiveAsrConfig']['finalDecode'], 'tail')

    def test_the_tail_pass_decodes_only_the_unconfirmed_words(self):
        audio = scripted_audio(9)
        inner, gate, started, seconds = FakeRecognizer(audio), threading.Event(), threading.Event(), []
        gate.set()

        def recorder(header, payload, cancel_event):
            started.set()
            gate.wait(5)
            seconds.append(len(payload) / 2 / RATE)
            return inner(header, payload, cancel_event)
        job = incremental.Job('job-' + 'u' * 20, False, [], ('model', 'cuda'), recorder)
        seq = feed(job, audio[:2 * 6 * RATE], pause=.01)        # passes confirm the early words
        for _ in range(100):
            if len(job.agreement.committed) >= 3:
                break
            time.sleep(.02)
        gate.clear()
        started.clear()
        seq += 1
        job.append(seq, audio[2 * 6 * RATE:2 * 7 * RATE])
        self.assertTrue(started.wait(5))                          # a pass is in flight when the user stops
        for start in range(2 * 7 * RATE, len(audio), RATE):
            seq += 1
            job.append(seq, audio[start:start + RATE])
        outcome = {}
        finisher = threading.Thread(target=lambda: outcome.update(value=job.finish(len(audio) // 2, seq, timeout=10)))
        finisher.start()
        time.sleep(.05)
        gate.set()
        finisher.join(10)
        result, reason = outcome['value']
        self.assertIsNone(reason)
        self.assertEqual(result['text'].split(), ['سلام', 'عايز', 'redesign', 'للصفحة', 'الرئيسية'])
        self.assertEqual(result['effectiveAsrConfig']['finalDecode'], 'tail')
        self.assertLess(seconds[-1], 6)                           # confirmed words are not decoded again
        self.assertIn('سلام', inner.calls[-1]['context'])         # they are the tail pass's prompt instead

    def test_inline_receives_word_times_on_the_recording_timeline(self):
        audio = scripted_audio()
        job = incremental.Job('job-' + 'k' * 20, True, [], ('model', 'cuda'), FakeRecognizer(audio))
        result, reason = job.finish(len(audio) // 2, feed(job, audio, pause=.01), timeout=10)
        self.assertIsNone(reason)
        times = [w['start_s'] for segment in result['segments'] for w in segment['words']]
        self.assertEqual([round(t, 1) for t in times], [at for _, at in SCRIPT])

    def test_a_short_utterance_is_read_whole(self):
        audio = scripted_audio(6)[:6 * RATE * 2]
        job = incremental.Job('job-' + 'i' * 20, False, [], ('model', 'cuda'), FakeRecognizer(audio))
        result, reason = job.finish(len(audio) // 2, feed(job, audio), timeout=10)
        self.assertIsNone(reason)
        self.assertEqual(result['text'].split(), ['سلام', 'عايز', 'redesign'])

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

    def test_a_failed_pass_requests_the_whole_recording(self):
        audio = scripted_audio()
        job = incremental.Job('job-' + 'c' * 20, False, [], ('model', 'cuda'), FakeRecognizer(audio, fail_on=1))
        result, reason = job.finish(len(audio) // 2, feed(job, audio), timeout=10)
        self.assertIsNone(result)
        self.assertTrue(reason.startswith('failed'))

    def test_missing_audio_at_stop_requests_the_whole_recording(self):
        audio = scripted_audio()
        job = incremental.Job('job-' + 'd' * 20, False, [], ('model', 'cuda'), FakeRecognizer(audio))
        last = feed(job, audio)
        result, reason = job.finish(len(audio) // 2 + 1, last, timeout=10)
        self.assertIsNone(result)
        self.assertEqual(reason, 'missing audio')

    def test_passes_that_cannot_keep_up_stop_instead_of_queueing(self):
        audio = scripted_audio(60)
        recognizer = FakeRecognizer(audio, delay=.5)
        job = incremental.Job('job-' + 'e' * 20, False, [], ('model', 'cpu'), recognizer)
        last = feed(job, audio)          # 60 s arrive at once: far beyond what one pass may decode
        result, reason = job.finish(len(audio) // 2, last, timeout=10)
        self.assertIsNone(result)
        self.assertEqual(reason, 'backlog')
        self.assertLessEqual(len(recognizer.calls), 2)
        self.assertTrue(all(len(call) for call in recognizer.calls))

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
        self.assertEqual((job.windows, bytes(job.pcm), job.provisional, job.agreement.committed), ([], b'', '', []))
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
