# PyInstaller folder build (faster start, fewer antivirus false positives than one file).
# Run through scripts/build.ps1: python -m PyInstaller installer/ArabicRelay.spec --distpath dist --workpath build/pyinstaller
import os
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

ROOT = os.path.abspath(os.path.join(SPECPATH, '..'))

a = Analysis(
    [os.path.join(SPECPATH, 'launch.py')],
    pathex=[os.path.join(ROOT, 'src')],
    datas=collect_data_files('arabic_relay') + collect_data_files('faster_whisper'),
    binaries=collect_dynamic_libs('ctranslate2'),
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Arabic Relay',
    console=False,
    upx=False,
    icon=os.path.join(ROOT, 'src', 'arabic_relay', 'assets', 'relay.ico'),
)
coll = COLLECT(exe, a.binaries, a.datas, upx=False, name='Arabic Relay')
