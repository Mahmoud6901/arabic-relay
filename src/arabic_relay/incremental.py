"""Processing during speech, in the style of Hex: continuous passes over the unconfirmed audio while the user talks.

Whisper is an offline model, so this is not a native streaming recognizer. While the user speaks, Relay decodes the
audio that is not confirmed yet, again and again: a pass starts as soon as the previous one ends and enough new audio
has arrived. A word is confirmed once two consecutive passes read it the same way (local agreement), and confirmed
audio is trimmed from later passes at a pause, so each pass stays short. No pass runs while the user is silent:
once the latest pass has heard the last word and a little silence after it, the GPU waits for new speech. So at
Stop the latest reading is usually final already and no decode remains; otherwise one last pass decodes the tail.

Anything uncertain (a failed decode, inconsistent timestamps, audio that arrived out of order, a buffer that never
confirms) falls back to decoding the whole recording with the baseline settings.

Passes read a recording piece by piece and lose Arabic words in mixed speech, so a short recording on the GPU is read
once more, whole, at Stop (see speech.stream_finish). What the passes heard is then only the provisional text.
"""
import logging
import re
import threading
import time
import unicodedata as ud

RATE = 16000
MAX_SECONDS = 300
MAX_SAMPLES = MAX_SECONDS * RATE
MAX_CHUNK = 2 * RATE                 # samples per transport chunk
FIRST_PASS = 1.0                     # seconds of audio before the first pass
STEP = 0.5                           # new audio a later pass waits for
KEEP = 5.0                           # a trimmed buffer still holds this much recent audio (context, language detection)
TRIM_AT = 10.0                       # the unconfirmed buffer is trimmed at a confirmed pause once longer than this
FORCE_TRIM_AT = 20.0                 # without a pause, at the last confirmed word once longer than this
BUFFER_CAP = 28.0                    # the worker decodes at most 30 s at once; beyond this, the whole clip after Stop
TAIL_MARGIN = 0.2                    # a final pass must end with this much silence, so its last word is whole
TRIM_GAP = 0.15                      # a pause between two words long enough to cut the buffer in
PAUSE_RATIO = 0.12                   # a frame quieter than this fraction of the speech level counts as silence
CONTEXT_CHARS = 200                  # confirmed text before the buffer, given to the next pass as its prompt
KEPT_RESULTS = 3                     # older passes keep only their position, not their decoded words
JOB_ID = re.compile(r'[A-Za-z0-9_-]{20,100}')
LANGUAGE = re.compile(r'[a-z]{2,3}')
IDLE_EXPIRY = 45                     # seconds without a chunk before an unfinished recording is discarded
WHOLE_READING = 'whole reading'      # why passes end when Stop will read the whole recording: expected, not a failure


class Ambiguous(Exception):
    pass


# --- Pure helpers (tested directly) ----------------------------------------------------------------------------

def frame_energy(samples, frame=1600):
    """RMS per frame (100 ms by default) of int16 samples."""
    import numpy as np
    count = len(samples) // frame
    if not count:
        return np.zeros(0)
    blocks = np.asarray(samples[:count * frame], dtype=np.float32).reshape(count, frame)
    return np.sqrt(np.mean(blocks * blocks, axis=1))


def speech_level(samples):
    """The loud end of the speech in `samples` (int16): the 90th percentile of 100 ms frame levels."""
    import numpy as np
    energy = frame_energy(samples)
    return float(np.percentile(energy, 90)) if len(energy) else 0.0


def silent(tail, level):
    """True when `tail` (int16 samples) holds no speech, judged against the recording's speech `level`."""
    import numpy as np
    if not len(tail):
        return True
    threshold = max(PAUSE_RATIO * level, 30.0)
    frames = frame_energy(tail, 400)                 # 25 ms frames: a short tail still gets several
    if not len(frames):
        frames = np.sqrt(np.mean(np.asarray(tail, dtype=np.float32) ** 2, keepdims=True))
    return bool((frames < threshold).all())


ARABIC_FOLD = str.maketrans({'أ': 'ا', 'إ': 'ا', 'آ': 'ا', 'ٱ': 'ا', 'ى': 'ي', 'ة': 'ه', 'ـ': None})


def normalize(word):
    text = ud.normalize('NFKC', word).casefold().translate(ARABIC_FOLD)
    return ''.join(c for c in text if not ud.category(c).startswith(('P', 'M', 'Z', 'C', 'S')))


def key(word):
    """What two passes must agree on: the normalized word, or the raw token for punctuation-only tokens."""
    return normalize(word['text']) or word['text'].strip()


def middle(word):
    return (word['start_s'] + word['end_s']) / 2


def window_words(window):
    """A pass's Whisper words moved onto the recording timeline, with their segment metrics."""
    offset = window['start'] / RATE
    duration = (window['end'] - window['start']) / RATE
    words = []
    for index, segment in enumerate(window['result'].get('segments') or []):
        items = segment.get('words') or []
        if segment.get('text', '').strip() and not items:
            raise Ambiguous('segment without word timestamps')
        for item in items:
            start, end = item.get('start_s'), item.get('end_s')
            if not isinstance(start, (int, float)) or not isinstance(end, (int, float)) or end < start:
                raise Ambiguous('inconsistent timestamps')
            if start < -0.2 or end > duration + 0.2:
                raise Ambiguous('timestamp outside window')
            words.append({'start_s': start + offset, 'end_s': end + offset, 'text': item.get('text', ''),
                          'probability': item.get('probability'), 'window': window['index'], 'segment': index,
                          'metrics': {name: segment.get(name) for name in ('avg_logprob', 'no_speech_prob', 'compression_ratio')}})
    for before, after in zip(words, words[1:]):
        if middle(after) + 0.05 < middle(before):
            raise Ambiguous('timestamps out of order')
    return words


class Agreement:
    """Local agreement over consecutive passes: a word is confirmed when two passes in a row read it alike."""

    def __init__(self):
        self.committed = []      # confirmed words on the recording timeline
        self.tentative = []      # the latest pass's unconfirmed words

    def last_time(self):
        return self.committed[-1]['end_s'] if self.committed else 0.0

    def fresh(self, words):
        """The words of a pass that are not confirmed yet: later than the last confirmed word, without the
        confirmed words that a pass over untrimmed audio hears again (matched as a run of up to five words)."""
        if not self.committed:
            return list(words)
        last = self.last_time()
        words = [word for word in words if word['start_s'] > last - 0.1]
        if words and abs(words[0]['start_s'] - last) < 1:
            for n in range(min(len(self.committed), len(words), 5), 0, -1):
                if [key(w) for w in self.committed[-n:]] == [key(w) for w in words[:n]]:
                    return words[n:]
        return words

    def update(self, words):
        """Confirm the longest prefix this pass shares with the previous pass; returns the newly confirmed words."""
        words = self.fresh(words)
        confirmed = []
        for previous, current in zip(self.tentative, words):
            if key(previous) != key(current):
                break
            confirmed.append(current)
        self.committed += confirmed
        self.tentative = words[len(confirmed):]
        return confirmed


def trim_point(committed, tentative, buffer_start, available):
    """Where the next pass may start, in seconds, or None. Only confirmed words before the last KEEP seconds are cut
    away, and only in a pause between two words or at the end of a Whisper segment, so no word is split."""
    length = available - buffer_start
    if length <= TRIM_AT:
        return None
    limit = available - KEEP
    words = [w for w in committed if w['start_s'] >= buffer_start]
    sequence = words + tentative
    best = None
    for index, word in enumerate(words):
        if word['end_s'] > limit:
            break
        following = sequence[index + 1] if index + 1 < len(sequence) else None
        if following is None:
            continue
        gap = following['start_s'] - word['end_s']
        boundary = following['window'] == word['window'] and following['segment'] != word['segment']
        if gap >= TRIM_GAP or (boundary and gap >= 0):
            best = word['end_s'] + gap / 2
    if best is None and length > FORCE_TRIM_AT:
        ended = [w for w in words if w['end_s'] <= available - 1]
        if ended:
            best = ended[-1]['end_s']
    return best if best is not None and best > buffer_start else None


def tail_cut(committed, tentative, buffer_start):
    """At Stop: where the tail pass starts, in seconds. Only the unconfirmed words still need decoding, so the pass
    starts in the latest pause before them; without a pause, at the start of the last confirmed word, which the pass
    hears again and drops. Confirmed text before the cut is the pass's prompt."""
    words = [w for w in committed if w['start_s'] >= buffer_start]
    if not words:
        return buffer_start
    sequence = words + tentative[:1]
    for before, after in reversed(list(zip(sequence, sequence[1:]))):
        if after['start_s'] - before['end_s'] >= TRIM_GAP:
            return (before['end_s'] + after['start_s']) / 2
    return words[-1]['start_s']


def assemble(words):
    """Text plus Inline-compatible segments rebuilt from the kept words, on the recording timeline."""
    text, segments, previous = '', [], None
    for word in words:
        piece = word['text']
        if previous is not None and previous['window'] != word['window'] and not piece[:1].isspace() and not text[-1:].isspace():
            piece = ' ' + piece
        text += piece
        segment_key = (word['window'], word['segment'])
        if not segments or segments[-1]['key'] != segment_key:
            segments.append({'key': segment_key, 'start_s': word['start_s'], 'end_s': word['end_s'], 'text': '',
                             **word['metrics'], 'words': []})
        segment = segments[-1]
        segment['end_s'] = word['end_s']
        segment['text'] += piece
        segment['words'].append({'start_s': word['start_s'], 'end_s': word['end_s'], 'text': piece,
                                 'probability': word['probability']})
        previous = word
    for segment in segments:
        del segment['key']
    return text.strip(), segments


# --- Recording jobs ------------------------------------------------------------------------------------------

class Job:
    """One recording's PCM, its passes and its provisional text. Owned by its unguessable job ID."""

    def __init__(self, job_id, inline_mode, keywords, key, transcriber, rules=None):
        self.id = job_id
        self.inline = inline_mode
        self.keywords = list(keywords)
        self.key = key                     # model folder and device, snapshotted at job start
        self.rules = rules                 # approved vocabulary rules in force when the recording started
        self.transcriber = transcriber     # callable(header, payload, cancel_event) -> worker result
        self.pcm = bytearray()
        self.next_seq = 0
        self.windows = []                  # passes, oldest first: index, start, end (samples), newest results
        self.agreement = Agreement()
        self.buffer_start = 0              # samples: where the unconfirmed audio begins
        self.decoded_to = 0                # samples: the end of the audio the latest pass decoded
        self.level = 0.0                   # the loudest speech level any pass has decoded
        self.language = None               # detected by the first pass that heard words; later passes reuse it
        self.final_words = None
        self.final_decode = None           # 'none' when the latest pass was already final, 'tail' otherwise
        self.tail_seconds = 0.0
        self.closed = False
        self.cancelled = threading.Event()
        self.stopped_reason = None         # why processing during speech stopped early
        self.provisional = ''
        self.condition = threading.Condition()
        self.created = self.touched = time.monotonic()
        self.times = {'start': self.created}
        self.inference_ms = []
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def read(self, low, high):
        import numpy as np
        return np.frombuffer(bytes(self.pcm[low * 2:high * 2]), dtype='<i2')

    def append(self, seq, payload):
        with self.condition:
            if self.closed or self.cancelled.is_set():
                raise ValueError('This recording has already finished.')
            if type(seq) is not int or seq != self.next_seq:
                # Never silently duplicate or drop audio: a gap or repeat ends processing during speech.
                raise ValueError('Recording audio arrived out of order.')
            if not payload or len(payload) % 2 or len(payload) > MAX_CHUNK * 2:
                raise ValueError('Invalid recording audio.')
            if len(self.pcm) + len(payload) > MAX_SAMPLES * 2:
                raise ValueError('Use a recording under five minutes.')
            if not self.pcm:
                self.times['firstAudio'] = time.monotonic()
            self.pcm += payload
            self.next_seq += 1
            self.touched = time.monotonic()
            self.condition.notify_all()
            return {'seq': seq, 'samples': len(self.pcm) // 2, 'provisional': self.provisional,
                    'partial': self.stopped_reason is None}

    def stop_partials(self, reason):
        if self.stopped_reason is None:
            self.stopped_reason = reason
            logging.info('Incremental dictation: processing during speech stopped (%s)', reason.split(':')[0])

    def context(self):
        """Confirmed text before the buffer: the prompt that keeps a trimmed pass consistent with what came before."""
        before = [w['text'] for w in self.agreement.committed if middle(w) < self.buffer_start / RATE]
        return ''.join(before).strip()[-CONTEXT_CHARS:]

    def final_ready(self, available):
        """The latest pass is final when its last moments and everything since are silence: it heard the last word
        whole, and nothing was said after it."""
        if not self.windows or self.decoded_to <= self.buffer_start:
            return False
        start = max(self.buffer_start, self.decoded_to - int(TAIL_MARGIN * RATE))
        return silent(self.read(start, available), self.level)

    def due(self, available):
        """Whether the next pass during recording should start: enough new audio, and some of it is speech."""
        needed = (STEP if self.windows else FIRST_PASS) * RATE
        if available - max(self.decoded_to, self.buffer_start) < needed:
            return False
        return not self.final_ready(available)

    def run(self):
        while True:
            with self.condition:
                while True:
                    if self.cancelled.is_set() or self.stopped_reason is not None:
                        return
                    available = len(self.pcm) // 2
                    if self.closed or self.due(available):
                        break
                    self.condition.wait(.25)
                final = self.closed
                if available - self.buffer_start > BUFFER_CAP * RATE:
                    # Passes cannot keep up or never agree: Stop decodes the whole recording instead.
                    self.stop_partials('backlog')
                    self.condition.notify_all()
                    return
                if final and (available <= self.buffer_start or self.final_ready(available)):
                    self.final_words = self.agreement.committed + self.agreement.tentative
                    self.final_decode = 'none'
                    self.tail_seconds = 0.0
                    self.condition.notify_all()
                    return
                if final:
                    cut = tail_cut(self.agreement.committed, self.agreement.tentative, self.buffer_start / RATE)
                    self.buffer_start = min(available, max(self.buffer_start, int(cut * RATE)))
                start, end = self.buffer_start, available
                payload = bytes(self.pcm[start * 2:end * 2])
                header = {'format': 'pcm16', 'window': True, 'inline': self.inline, 'keywords': self.keywords,
                          'context': self.context(), **({'language': self.language} if self.language else {})}
            index = len(self.windows)
            started = time.monotonic()
            try:
                result = self.transcriber(header, payload, self.cancelled)
            except Exception as error:
                with self.condition:
                    self.stop_partials('failed: ' + getattr(error, 'code', type(error).__name__))
                    self.condition.notify_all()
                return
            finally:
                payload = None
            with self.condition:
                if self.cancelled.is_set():
                    return                # results of a cancelled job are invalid
                self.inference_ms.append(round((time.monotonic() - started) * 1000))
                window = {'index': index, 'start': start, 'end': end, 'result': result}
                try:
                    words = window_words(window)
                except Ambiguous as error:
                    self.stop_partials('seam: ' + str(error))
                    self.condition.notify_all()
                    return
                self.windows.append(window)
                self.level = max(self.level, speech_level(self.read(start, end)))
                detected = ((result.get('effectiveAsrConfig') or {}).get('detectedLanguage'))
                if words and self.language is None and isinstance(detected, str) and LANGUAGE.fullmatch(detected):
                    self.language = detected
                for old in self.windows[:-KEPT_RESULTS]:
                    old.pop('result', None)
                self.decoded_to = end
                if final:
                    # The tail after Stop: everything this pass heard that is not confirmed is final.
                    self.final_words = self.agreement.committed + self.agreement.fresh(words)
                    self.final_decode = 'tail'
                    self.tail_seconds = (end - start) / RATE
                    self.condition.notify_all()
                    return
                self.agreement.update(words)
                cut = trim_point(self.agreement.committed, self.agreement.tentative, self.buffer_start / RATE, end / RATE)
                if cut is not None:
                    self.buffer_start = min(end, max(self.buffer_start, int(cut * RATE)))
                self.provisional = assemble(self.agreement.committed + self.agreement.tentative)[0]
                self.times.setdefault('firstPartial', time.monotonic())
                self.condition.notify_all()

    def on_gpu(self):
        """False once a pass reports that the worker moved to the CPU after a device fault."""
        with self.condition:
            last = next((w['result'] for w in reversed(self.windows) if 'result' in w), {})
        return self.key[1] == 'cuda' and last.get('device', 'GPU') == 'GPU'

    def finish(self, total_samples, last_seq, timeout, whole=False):
        """Close input and wait (bounded) for the final reading; returns (result or None, reason). With `whole`, the
        caller will read the whole recording itself: a pass in flight may end, but no tail pass starts."""
        with self.condition:
            if self.cancelled.is_set():
                raise ValueError('This recording was cancelled.')
            if last_seq != self.next_seq - 1 or total_samples != len(self.pcm) // 2:
                self.stop_partials('missing audio')
            if whole:
                self.stop_partials(WHOLE_READING)
            self.closed = True
            self.times['stop'] = time.monotonic()
            self.condition.notify_all()
        self.thread.join(timeout)
        with self.condition:
            if self.thread.is_alive():
                self.stop_partials('timeout')
            if self.stopped_reason is not None:
                return None, self.stopped_reason
            if self.final_words is None:
                return None, 'missing coverage'
            text, segments = assemble(self.final_words)
            last = next((w['result'] for w in reversed(self.windows) if 'result' in w), {})
            return {'text': text, 'segments': segments, 'device': last.get('device', 'GPU'),
                    'fallback': bool(last.get('fallback')),
                    'seconds': len(self.pcm) / 2 / RATE,
                    'effectiveAsrConfig': {**(last.get('effectiveAsrConfig') or {}), 'path': 'incremental',
                                           'passes': len(self.windows), 'finalDecode': self.final_decode,
                                           'tailSeconds': round(self.tail_seconds, 2)}}, None

    def cancel(self):
        self.cancelled.set()
        with self.condition:
            self.pcm = bytearray()
            self.windows = []
            self.agreement = Agreement()
            self.provisional = ''
            self.condition.notify_all()


class Registry:
    """At most one active recording job. Its ID is the only handle; another surface cannot reach it."""

    def __init__(self, on_release=None):
        self.lock = threading.Lock()
        self.job = None
        self.on_release = on_release or (lambda job: None)

    def start(self, job_id, factory):
        if not isinstance(job_id, str) or not JOB_ID.fullmatch(job_id):
            raise ValueError('Invalid recording request.')
        with self.lock:
            self.expire_locked()
            if self.job is not None:
                return None
            self.job = factory()
            return self.job

    def get(self, job_id):
        with self.lock:
            self.expire_locked()
            if self.job is None or not isinstance(job_id, str) or job_id != self.job.id:
                raise ValueError('This recording is no longer active.')
            return self.job

    def release(self, job):
        with self.lock:
            if self.job is not job:
                return False
            self.job = None
        job.cancel()
        self.on_release(job)
        return True

    def expire(self):
        with self.lock:
            self.expire_locked()

    def expire_locked(self):
        job = self.job
        if job is not None and not job.closed and time.monotonic() - job.touched > IDLE_EXPIRY:
            logging.info('Incremental dictation: abandoned recording discarded')
            self.job = None
            job.cancel()
            self.on_release(job)


REGISTRY = Registry()
