"""Processing during speech: overlapping offline Whisper windows over a bounded 16 kHz PCM recording.

This is incremental processing around an offline model, not a streaming recognizer. Every window is decoded
whole; neighbouring windows overlap by two seconds around a proposed pause. The final transcript joins windows at
those pauses using absolute word timestamps. Anything uncertain (a missing window, inconsistent timestamps, an
ambiguous seam, a failed decode, a backlog) falls back to decoding the complete recording.
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
WINDOW_TARGET = 12.0                 # prototype parameters: tunable candidates, not approved thresholds
CUT_SEARCH = (7.0, 14.0)             # where a cut may fall, relative to the window start
CUT_TARGET = 12.0                    # the pause nearest this point wins
HALF_OVERLAP = 1.0                   # without a real pause: each window runs this far past its cut, the next starts this far before
MIN_SEARCH = 3.0                     # seconds of audio the pause search needs before a window is planned
PAUSE_RATIO = 0.12                   # a frame quieter than this fraction of the window's speech level counts as pause
MIN_PAUSE_FRAMES = 3                 # 300 ms
WINDOW_CAP = 15.0
BACKLOG_LIMIT = 30.0                 # unprocessed recorded audio before partial inference stops
FINAL_LIMIT = 30.0                   # audio still unprocessed at Stop that windows may finish; beyond, whole clip
SEAM_MARGIN = 0.6
SEAM_EDGE = 0.25
JOB_ID = re.compile(r'[A-Za-z0-9_-]{20,100}')
IDLE_EXPIRY = 45                     # seconds without a chunk before an unfinished recording is discarded


class Ambiguous(Exception):
    pass


# --- Pure helpers (tested directly) ----------------------------------------------------------------------------

def frame_energy(samples, frame=1600):
    """RMS per 100 ms frame of int16 samples."""
    import numpy as np
    count = len(samples) // frame
    if not count:
        return np.zeros(0)
    blocks = np.asarray(samples[:count * frame], dtype=np.float32).reshape(count, frame)
    return np.sqrt(np.mean(blocks * blocks, axis=1))


def plan_cut(read, start, available, final=False):
    """The next window (start, end, cut, half) in samples, or None when more audio is needed.

    `read(low, high)` returns int16 samples in [low, high). A cut is a proposed pause, never permission to drop
    audio: the window runs `half` samples past the cut and the next one starts `half` before it. When the search
    range holds a real pause (a quiet run of at least 300 ms, relative to this window's speech level), the cut is
    its centre and both window edges stay inside the silence, so no word is split. Otherwise the cut is the
    quietest frame and the windows overlap by two seconds for the seam to be reconciled from both sides.
    """
    remaining = available - start
    if remaining <= 0:
        return None
    if final and remaining <= WINDOW_CAP * RATE:
        return start, available, None, 0
    if remaining < (CUT_SEARCH[0] + MIN_SEARCH) * RATE:
        return None                     # too little audio to look for a pause without cutting blind
    low = start + int(CUT_SEARCH[0] * RATE)
    # Search as far as the target allows, but always leave room to extend past the cut for the overlap.
    high = min(start + int(CUT_SEARCH[1] * RATE), available - int(HALF_OVERLAP * RATE))
    if high <= low:
        return None
    energy = frame_energy(read(low, high))
    if not len(energy):
        return None
    import numpy as np
    level = float(np.percentile(frame_energy(read(start, high)), 90))
    quiet = energy < max(PAUSE_RATIO * level, 30.0)
    runs, begin = [], None
    for index, silent in enumerate(list(quiet) + [False]):
        if silent and begin is None:
            begin = index
        elif not silent and begin is not None:
            runs.append((begin, index))
            begin = None
    runs = [run for run in runs if run[1] - run[0] >= MIN_PAUSE_FRAMES]
    if runs:
        # The pause nearest the target length; longer pauses win ties so both window edges stay in the silence.
        first, last = min(runs, key=lambda run: (abs(low + (run[0] + run[1]) * 800 - (start + int(CUT_TARGET * RATE))),
                                                 -(run[1] - run[0])))
        cut = low + (first + last) * 800
        half = min(int(HALF_OVERLAP * RATE), max(int(.15 * RATE), (last - first) * 800 - 800))
    else:
        target = start + int(CUT_TARGET * RATE)
        near = [i for i in range(len(energy)) if abs(low + (i + .5) * 1600 - target) <= 2 * RATE]
        index = min(near, key=lambda i: energy[i]) if near else int(energy.argmin())
        cut = low + index * 1600 + 800
        half = int(HALF_OVERLAP * RATE)
    # Never let a window grow past the cap, whatever the cut.
    cut = min(cut, start + int(WINDOW_CAP * RATE) - half)
    return start, cut + half, cut, half


ARABIC_FOLD = str.maketrans({'أ': 'ا', 'إ': 'ا', 'آ': 'ا', 'ٱ': 'ا', 'ى': 'ي', 'ة': 'ه', 'ـ': None})


def normalize(word):
    text = ud.normalize('NFKC', word).casefold().translate(ARABIC_FOLD)
    return ''.join(c for c in text if not ud.category(c).startswith(('P', 'M', 'Z', 'C', 'S')))


def middle(word):
    return (word['start_s'] + word['end_s']) / 2


def window_words(window):
    """Window-local Whisper words moved onto the recording timeline, with their segment metrics."""
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
                          'metrics': {key: segment.get(key) for key in ('avg_logprob', 'no_speech_prob', 'compression_ratio')}})
    for before, after in zip(words, words[1:]):
        if middle(after) + 0.05 < middle(before):
            raise Ambiguous('timestamps out of order')
    return words


def same_word(a, b):
    return normalize(a['text']) == normalize(b['text']) and abs(middle(a) - middle(b)) <= 0.6


def align(previous, following):
    """Longest common subsequence where a match needs the same normalized word at nearly the same time, so
    repeated words pair with their own occurrence rather than a neighbour."""
    n, m = len(previous), len(following)
    table = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            table[i][j] = (table[i + 1][j + 1] + 1 if same_word(previous[i], following[j])
                           else max(table[i + 1][j], table[i][j + 1]))
    i = j = 0
    while i < n and j < m:
        if same_word(previous[i], following[j]) and table[i][j] == table[i + 1][j + 1] + 1:
            yield 'both', previous[i], following[j]
            i, j = i + 1, j + 1
        elif table[i + 1][j] >= table[i][j + 1]:
            yield 'previous', previous[i], None
            i += 1
        else:
            yield 'following', None, following[j]
            j += 1
    for word in previous[i:]:
        yield 'previous', word, None
    for word in following[j:]:
        yield 'following', None, word


def overlap(a, b):
    """Seconds two words share on the recording timeline."""
    return min(a['end_s'], b['end_s']) - max(a['start_s'], b['start_s'])


def substitution_groups(previous, following):
    """Connected groups of unmatched words whose times overlap across the two windows."""
    nodes = [('p', w) for w in previous] + [('f', w) for w in following]
    parent = list(range(len(nodes)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i, (side_a, a) in enumerate(nodes):
        for j, (side_b, b) in enumerate(nodes):
            if side_a == 'p' and side_b == 'f':
                shortest = min(a['end_s'] - a['start_s'], b['end_s'] - b['start_s'])
                if overlap(a, b) > max(.05, .3 * shortest):
                    parent[root(i)] = root(j)
    groups = {}
    for i, node in enumerate(nodes):
        groups.setdefault(root(i), []).append(node)
    return list(groups.values())


# Matched against normalized words (punctuation removed, so "don't" is "dont"). The last branch is the Egyptian
# circumfix negation م…ش (متغيرش، ماتمسحش).
NEGATION = re.compile(r'^(?:و)?(?:مش|ما|مفيش|مافيش|لا|لم|لن|ليس|مو|not|no|never|dont|doesnt|didnt|wont|cant)$|^م.+ش$', re.I)
NUMBER_WORD = re.compile(r'[0-9]|[٠-٩]|واحد|اتنين|اثنين|تلات|ثلاث|اربع|أربع|خمس|ست|سبع|تمان|ثمان|تسع|عشر|مية|مائة|ميت|الف|ألف|'
                         r'zero|one|two|three|four|five|six|seven|eight|nine|ten|twenty|thirty|hundred|thousand', re.I)


def variants(mine, theirs):
    """True when two readings of one moment are spelling variants of the same words, not different words.
    Numbers and negation must agree exactly; anything else needs the joined letters to be close."""
    a = ''.join(normalize(w['text']) for w in mine)
    b = ''.join(normalize(w['text']) for w in theirs)
    if not a or not b:
        return False
    for check in (lambda w: bool(NEGATION.search(normalize(w['text']))), lambda w: bool(NUMBER_WORD.search(w['text']))):
        if any(map(check, mine + theirs)) and a != b:
            return False
    import difflib
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio() >= .6


def merge_seam(previous, following, cut, edges, previous_all, following_all):
    """Join the words both windows saw near one cut.

    A word both windows heard alike is kept once. Where the windows heard the same moment differently (a spelling
    variant or a word split differently), the version decoded further from its window's edge, with more context,
    is kept. A word only one window heard is kept unless it is a stretched edge artifact, or the other window also
    heard that span (then a drop or duplicate cannot be ruled out): Ambiguous.
    `edges` is (end of the previous window, start of the following window) in seconds; `*_all` are whole-window
    word lists used to prove that the other window produced nothing over a word's time span.
    """
    joined, left, right = [], [], []
    for kind, a, b in align(previous, following):
        if kind == 'both':
            joined.append(a if (middle(a) + middle(b)) / 2 < cut else b)
        elif kind == 'previous':
            left.append(a)
        else:
            right.append(b)
    for group in substitution_groups(left, right):
        mine = [w for side, w in group if side == 'p']
        theirs = [w for side, w in group if side == 'f']
        if mine and theirs and variants(mine, theirs):
            centre = sum(map(middle, mine + theirs)) / len(group)
            joined += mine if edges[0] - centre >= centre - edges[1] else theirs
            continue
        for side, word in group:
            if side == 'p' and middle(word) < cut - SEAM_EDGE:
                joined.append(word)          # the earlier window heard it with full context
            elif side == 'f' and middle(word) >= cut + SEAM_EDGE:
                joined.append(word)          # the later window heard it with full context
            elif any(overlap(word, other) > .15 for other in (following_all if side == 'p' else previous_all)):
                # Both windows produced words over this span and they disagree: a drop or duplicate is possible.
                raise Ambiguous('unmatched word at the cut')
            else:
                joined.append(word)          # only one window heard anything here, so nothing can be lost or doubled
    return sorted(joined, key=middle)


def reconcile(windows):
    """All windows, in order, to one word list. Raises Ambiguous rather than guessing."""
    if not windows or windows[0]['start'] != 0:
        raise Ambiguous('missing coverage')
    for before, after in zip(windows, windows[1:]):
        if before['cut'] is None or not before['start'] < after['start'] < before['cut'] < before['end']:
            raise Ambiguous('missing coverage')
        if after['start'] != before['cut'] - before.get('half', int(HALF_OVERLAP * RATE)):
            raise Ambiguous('missing coverage')
    words = window_words(windows[0])
    output = []
    for index, window in enumerate(windows[1:], start=1):
        cut = windows[index - 1]['cut'] / RATE
        low, high = cut - SEAM_MARGIN, cut + SEAM_MARGIN
        # The same span from both windows; outside it, each window keeps only its own side.
        output += [word for word in words if middle(word) < low]
        following = window_words(window)
        output += merge_seam([word for word in words if low <= middle(word) < high],
                             [word for word in following if low <= middle(word) < high], cut,
                             (windows[index - 1]['end'] / RATE, window['start'] / RATE),
                             window_words(windows[index - 1]), following)
        words = [word for word in following if middle(word) >= high]
    return output + words


def assemble(words):
    """Text plus Inline-compatible segments rebuilt from the kept words, on the recording timeline."""
    text, segments, previous = '', [], None
    for word in words:
        piece = word['text']
        if previous is not None and previous['window'] != word['window'] and not piece[:1].isspace() and not text[-1:].isspace():
            piece = ' ' + piece
        text += piece
        key = (word['window'], word['segment'])
        if not segments or segments[-1]['key'] != key:
            segments.append({'key': key, 'start_s': word['start_s'], 'end_s': word['end_s'], 'text': '',
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
    """One recording's PCM, its windows and its provisional text. Owned by its unguessable job ID."""

    def __init__(self, job_id, inline_mode, keywords, key, transcriber, rules=None):
        self.id = job_id
        self.inline = inline_mode
        self.keywords = list(keywords)
        self.rules = rules                 # approved vocabulary rules in force when the recording started
        self.key = key                     # model folder and device, snapshotted at job start
        self.transcriber = transcriber     # callable(header, payload, cancel_event) -> worker result
        self.pcm = bytearray()
        self.next_seq = 0
        self.windows = []
        self.next_start = 0
        self.closed = False
        self.cancelled = threading.Event()
        self.stopped_reason = None         # why partial inference stopped early
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
                # Never silently duplicate or drop audio: a gap or repeat ends incremental processing.
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
            logging.info('Incremental dictation: partial processing stopped (%s)', reason)

    def next_window(self):
        """Called with the condition held."""
        available = len(self.pcm) // 2
        limit = FINAL_LIMIT if self.closed else BACKLOG_LIMIT
        if available - self.next_start > limit * RATE:
            self.stop_partials('backlog')
            return None
        return plan_cut(self.read, self.next_start, available, final=self.closed)

    def run(self):
        while True:
            with self.condition:
                while True:
                    if self.cancelled.is_set() or self.stopped_reason is not None:
                        return
                    plan = self.next_window()
                    if plan is not None or self.stopped_reason is not None:
                        break
                    if self.closed:
                        return            # nothing left to decode
                    self.condition.wait(.5)
                if plan is None:
                    return
                start, end, cut, half = plan
                payload = bytes(self.pcm[start * 2:end * 2])
                # A recording that ends before any window was decoded is one whole clip. Craft mode then needs no
                # word timestamps to join anything, so it is decoded exactly as the whole-recording path decodes it
                # (measured: word timestamps added ~0.3 s after Stop to short Craft recordings).
                whole = start == 0 and cut is None and not self.windows and not self.inline
            index = len(self.windows)
            started = time.monotonic()
            try:
                # The same recognition settings as a whole recording in this writing mode, plus word timestamps.
                result = self.transcriber({'format': 'pcm16', 'window': not whole, 'inline': self.inline,
                                           'keywords': self.keywords}, payload, self.cancelled)
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
                elapsed = round((time.monotonic() - started) * 1000)
                self.inference_ms.append(elapsed)
                self.windows.append({'index': index, 'start': start, 'end': end, 'cut': cut, 'half': half,
                                     'result': result, 'whole': whole})
                if whole:
                    self.times.setdefault('firstPartial', time.monotonic())
                    self.condition.notify_all()
                    return
                self.next_start = end if cut is None else cut - half
                if not self.closed and cut is not None and elapsed > (end - start) / RATE * 1000:
                    # Decoding this window took longer than the audio it covered while more audio is still
                    # arriving: inference cannot catch up on this machine, so Stop decodes the whole recording
                    # with the baseline settings instead of spending more time on windows.
                    self.stop_partials('behind')
                    self.condition.notify_all()
                    return
                try:
                    # Provisional only: the newest window's tail can still change at the next seam.
                    self.provisional = assemble(reconcile(self.windows))[0]
                except Ambiguous as error:
                    self.stop_partials('seam: ' + str(error))
                self.times.setdefault('firstPartial', time.monotonic())
                self.condition.notify_all()
                if cut is None:
                    return

    def finish(self, total_samples, last_seq, timeout):
        """Close input and wait (bounded) for outstanding windows; returns (result or None, reason)."""
        with self.condition:
            if self.cancelled.is_set():
                raise ValueError('This recording was cancelled.')
            if last_seq != self.next_seq - 1 or total_samples != len(self.pcm) // 2:
                self.stop_partials('missing audio')
            self.closed = True
            self.times['stop'] = time.monotonic()
            self.condition.notify_all()
        self.thread.join(timeout)
        with self.condition:
            if self.thread.is_alive():
                self.stop_partials('timeout')
            if self.stopped_reason is not None:
                return None, self.stopped_reason
            if not self.windows or self.windows[-1]['cut'] is not None or self.windows[-1]['end'] != len(self.pcm) // 2:
                return None, 'missing coverage'
            if self.windows[0].get('whole'):
                only = self.windows[0]['result']
                text = only['text'] if isinstance(only.get('text'), str) else ''.join(
                    segment.get('text', '') for segment in only.get('segments') or [])
                return {'text': text.strip(), 'segments': [], 'device': only.get('device', 'GPU'),
                        'fallback': bool(only.get('fallback')), 'seconds': len(self.pcm) / 2 / RATE,
                        'effectiveAsrConfig': {'path': 'incremental', 'windows': 1, 'wholeClip': True,
                                               'wordTimestamps': False}}, None
            try:
                words = reconcile(self.windows)
            except Ambiguous as error:
                return None, 'seam: ' + str(error)
            text, segments = assemble(words)
            last = self.windows[-1]['result']
            return {'text': text, 'segments': segments, 'device': last.get('device', 'GPU'),
                    'fallback': any(w['result'].get('fallback') for w in self.windows),
                    'seconds': len(self.pcm) / 2 / RATE,
                    'effectiveAsrConfig': {**(last.get('effectiveAsrConfig') or {}), 'path': 'incremental',
                                           'windows': len(self.windows), 'windowSeconds': WINDOW_TARGET,
                                           'overlapSeconds': [round(2 * w['half'] / RATE, 2) for w in self.windows[:-1]]}}, None

    def cancel(self):
        self.cancelled.set()
        with self.condition:
            self.pcm = bytearray()
            self.windows = []
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
