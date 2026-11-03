"""Builds a SYNTHETIC Arabic-English development corpus with installed Windows OneCore voices.

Synthetic speech is not a substitute for consented human recordings: use it for transport, seam and regression
checks, never as evidence of accuracy on real users. Output goes to .local/corpus (ignored by git).

    python scripts/dev_corpus.py [--out .local/corpus]
"""
import argparse
import io
import json
from pathlib import Path
import subprocess
import tempfile
import wave

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
RATE = 16000
AR, EN = 'Microsoft Hoda', 'Microsoft Zira'

# (id, parts, reference, english terms, numbers [alternatives], negations [alternatives]).
# Parts in [brackets] are spoken by the English voice; the reference keeps the intended English spelling.
SENTENCES = {
    'dev': [
        ('d01', 'عايز أعمل [redesign] للصفحة الرئيسية، ومش عايز أغير الـ[login flow] خالص.', ['redesign', 'login flow'], [], [['مش']]),
        ('d02', 'ضيف [dark mode] في الـ[settings page]، وخلي الـ[toggle] يحفظ الاختيار.', ['dark mode', 'settings page', 'toggle'], [], []),
        ('d03', 'الـ[API] بترجع [error 404] لما الـ[user ID] يكون فاضي.', ['API', 'user ID'], [['404']], []),
        ('d04', 'اعمل [refactor] للـ[function] اللي اسمها [parse config]، ومتغيرش أي حاجة في الـ[tests].', ['refactor', 'function', 'parse config', 'tests'], [], [['متغيرش', 'ماتغيرش', 'ما تغيرش', 'متغيروش']]),
        ('d05', 'عندنا تلاتة [bugs] في الـ[checkout page]، أهمهم إن الـ[payment button] مش بيشتغل على الموبايل.', ['bugs', 'checkout page', 'payment button'], [['تلاتة', 'ثلاثة', '3']], [['مش']]),
        ('d06', 'رجع الـ[commit] اللي فات، وبعدين اعمل [push] على الـ[main branch].', ['commit', 'push', 'main branch'], [], []),
        ('d07', 'عايز [unit tests] للـ[React component] ده، وخلي الـ[coverage] فوق تمانين في المية.', ['unit tests', 'React component', 'coverage'], [['تمانين', 'ثمانين', '80']], []),
        ('d08', 'راجع صفحة تسجيل الدخول، وحسن رسائل الخطأ عشان تكون واضحة للمستخدم.', [], [], []),
        ('d09', '[Please add pagination to the orders table and keep the page size at twenty five.]', ['pagination', 'orders table', 'page size'], [['twenty five', 'twenty-five', '25']], []),
        ('d10', 'الملف اسمه [index.html] وموجود جوه فولدر الـ[source].', ['index.html', 'source'], [], []),
        ('d11', 'تاني تاني، الـ[build] فشل تاني، شوف الـ[logs] كويس.', ['build', 'logs'], [], []),
        ('d12', 'متمسحش الـ[database]، اعمل [backup] الأول وبعدين نشوف.', ['database', 'backup'], [], [['متمسحش', 'ماتمسحش', 'ما تمسحش']]),
        ('d13', 'خلي الـ[timeout] خمستاشر ثانية بدل تلاتين.', ['timeout'], [['خمستاشر', 'خمسة عشر', '15'], ['تلاتين', 'ثلاثين', '30']], []),
        ('d14', 'الـ[deadline] بعد الـ[meeting] بتاع يوم الخميس.', ['deadline', 'meeting'], [], []),
        ('d15', 'تمام، [ship it].', ['ship it'], [], []),
        ('d16', 'لأ، استنى.', [], [], [['لأ', 'لا']]),
        ('d17', 'عايز أعرف ليه الـ[memory usage] بيزيد كل ما نفتح [tab] جديدة.', ['memory usage', 'tab'], [], []),
        ('d18', 'اكتب [README] بالإنجليزي يشرح إزاي نعمل [install] و[setup].', ['README', 'install', 'setup'], [], []),
        ('d19', 'مش عايزين نستخدم [Redux]، خلينا على الـ[context API].', ['Redux', 'context API'], [], [['مش']]),
        ('d20', 'الـ[query] دي بطيئة جدا، ضيف [index] على عمود الـ[email].', ['query', 'index', 'email'], [], []),
    ],
    'heldout': [
        ('h01', 'عايز [feature flag] للـ[new dashboard] ومتفعلهوش غير للـ[admins].', ['feature flag', 'new dashboard', 'admins'], [], [['متفعلهوش', 'ماتفعلهوش', 'ما تفعلهوش']]),
        ('h02', 'صلح الـ[typo] اللي في الـ[header] وغير اللون للأزرق.', ['typo', 'header'], [], []),
        ('h03', 'الـ[CI pipeline] بياخد عشرين دقيقة، عايزين ننزله لخمسة.', ['CI pipeline'], [['عشرين', '20'], ['خمسة', '5']], []),
        ('h04', "[Please don't change the database schema without a migration.]", ['database schema', 'migration'], [], [["don't", 'do not']]),
        ('h05', 'اعمل [rename] للمتغير من [user name] لـ[display name].', ['rename', 'user name', 'display name'], [], []),
        ('h06', 'مفيش داعي نعمل [deploy] النهارده، استنى لبكرة.', ['deploy'], [], [['مفيش', 'ما فيش']]),
        ('h07', 'ضيف [loading spinner] لما الـ[request] ياخد أكتر من تانية.', ['loading spinner', 'request'], [], []),
        ('h08', 'الصور مش بتظهر في الـ[gallery] على [Safari] بس.', ['gallery', 'Safari'], [], [['مش']]),
        ('h09', 'حول الـ[script] ده لـ[TypeScript] وخلي الـ[types] صارمة.', ['script', 'TypeScript', 'types'], [], []),
        ('h10', 'اسأل الـ[backend team] عن الـ[rate limit] قبل ما نكمل.', ['backend team', 'rate limit'], [], []),
    ],
}

TTS = r'''param([string]$TextFile, [string]$Voice, [string]$Out)
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Media.SpeechSynthesis.SpeechSynthesizer, Windows.Media.SpeechSynthesis, ContentType = WindowsRuntime]
$null = [Windows.Storage.Streams.DataReader, Windows.Storage.Streams, ContentType = WindowsRuntime]
$asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' } | Select-Object -First 1
function Await($op, [Type]$type) { $task = $asTask.MakeGenericMethod($type).Invoke($null, @($op)); $task.Wait(-1) | Out-Null; $task.Result }
$synth = New-Object Windows.Media.SpeechSynthesis.SpeechSynthesizer
$chosen = [Windows.Media.SpeechSynthesis.SpeechSynthesizer]::AllVoices | Where-Object { $_.DisplayName -eq $Voice } | Select-Object -First 1
if (-not $chosen) { exit 3 }
$synth.Voice = $chosen
$text = [IO.File]::ReadAllText($TextFile, [Text.Encoding]::UTF8)
$stream = Await ($synth.SynthesizeSsmlToStreamAsync($text)) ([Windows.Media.SpeechSynthesis.SpeechSynthesisStream])
$reader = New-Object Windows.Storage.Streams.DataReader($stream.GetInputStreamAt(0))
$size = [uint32]$stream.Size
$null = Await ($reader.LoadAsync($size)) ([uint32])
$bytes = New-Object byte[] $size
$reader.ReadBytes($bytes)
[IO.File]::WriteAllBytes($Out, $bytes)
'''


def ssml(parts):
    body = ''
    for index, piece in enumerate(parts.replace(']', '[').split('[')):
        if not piece:
            continue
        piece = piece.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        body += f"<voice name='{EN}'>{piece}</voice>" if index % 2 else piece
    return f"<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' xml:lang='ar-EG'>{body}</speak>"


def reference(parts):
    return parts.replace('[', '').replace(']', '').strip()


def synthesize(folder, parts):
    text_file, out = folder / 'text.xml', folder / 'out.wav'
    text_file.write_text(ssml(parts), encoding='utf-8')
    done = subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(folder / 'tts.ps1'),
                           str(text_file), AR, str(out)], capture_output=True, text=True, timeout=120)
    if done.returncode:
        raise SystemExit(f'Speech synthesis failed ({done.returncode}): {done.stderr[-500:]}')
    with wave.open(io.BytesIO(out.read_bytes())) as clip:
        if (clip.getframerate(), clip.getnchannels(), clip.getsampwidth()) != (RATE, 1, 2):
            raise SystemExit('Unexpected synthesis format')
        return np.frombuffer(clip.readframes(clip.getnframes()), dtype='<i2').astype(np.float32)


def write(path, samples):
    samples = np.clip(samples, -32768, 32767).astype('<i2')
    with wave.open(str(path), 'wb') as clip:
        clip.setnchannels(1)
        clip.setsampwidth(2)
        clip.setframerate(RATE)
        clip.writeframes(samples.tobytes())


def silence(seconds):
    return np.zeros(int(seconds * RATE), dtype=np.float32)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--out', default=str(ROOT / '.local' / 'corpus'))
    args = parser.parse_args()
    out = Path(args.out)
    rng = np.random.default_rng(2026)
    manifest = {'synthetic': True, 'voices': [AR, EN], 'rate': RATE,
                'note': 'Synthetic Windows OneCore speech. Not consented human recordings; not evidence of real-user accuracy.',
                'normalization': 'NFKC, casefold, Arabic alef/ya/ta-marbuta folding, tatweel, diacritics and punctuation removed',
                'clips': []}
    with tempfile.TemporaryDirectory() as temp:
        temp = Path(temp)
        (temp / 'tts.ps1').write_text(TTS, encoding='utf-8')
        for split, sentences in SENTENCES.items():
            folder = out / split
            folder.mkdir(parents=True, exist_ok=True)
            audio = {}
            for sid, parts, terms, numbers, negations in sentences:
                audio[sid] = synthesize(temp, parts)
                clip = np.concatenate([silence(.4), audio[sid], silence(.6)])
                write(folder / f'{sid}.wav', clip)
                manifest['clips'].append({'id': f'{split}/{sid}', 'split': split, 'kind': 'sentence', 'seconds': len(clip) / RATE,
                                          'reference': reference(parts), 'terms': terms, 'numbers': numbers, 'negations': negations})
            ids = [s[0] for s in sentences]
            groups = [ids[i:i + 7] for i in range(0, len(ids), 7)]
            variants = [('pauses', lambda: rng.uniform(.3, 1.2), 1.0, None)]
            if split == 'dev':
                variants += [('continuous', lambda: .05, 1.0, None), ('quiet', lambda: rng.uniform(.3, 1.0), .15, None),
                             ('noisy', lambda: rng.uniform(.3, 1.0), 1.0, 10)]
            for variant, gap, gain, snr in variants:
                for number, group in enumerate(groups if variant == 'pauses' else groups[:1]):
                    pieces = [silence(.4)]
                    for sid in group:
                        pieces += [audio[sid], silence(gap())]
                    clip = np.concatenate(pieces) * gain
                    if snr is not None:
                        power = np.mean(clip[clip != 0] ** 2)
                        clip = clip + rng.standard_normal(len(clip)) * np.sqrt(power / 10 ** (snr / 10))
                    name = f'long-{variant}-{number + 1}'
                    write(folder / f'{name}.wav', clip)
                    rows = {s[0]: s for s in sentences}
                    manifest['clips'].append({'id': f'{split}/{name}', 'split': split, 'kind': variant, 'seconds': len(clip) / RATE,
                                              'reference': ' '.join(reference(rows[sid][1]) for sid in group),
                                              'terms': [t for sid in group for t in rows[sid][2]],
                                              'numbers': [n for sid in group for n in rows[sid][3]],
                                              'negations': [n for sid in group for n in rows[sid][4]], 'sentences': group})
    (out / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding='utf-8')
    total = sum(c['seconds'] for c in manifest['clips'])
    print(f"{len(manifest['clips'])} synthetic clips, {total / 60:.1f} minutes, in {out}")


if __name__ == '__main__':
    main()
