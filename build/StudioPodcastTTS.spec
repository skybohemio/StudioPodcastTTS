# -*- mode: python ; coding: utf-8 -*-
import os
from PyInstaller.utils.hooks import collect_submodules, collect_all
ROOT = os.path.dirname(os.path.abspath(SPECPATH))

import _ssl as _sslmod
_dlls_dir = os.path.dirname(_sslmod.__file__)
_ssl_bins = [(os.path.join(_dlls_dir, d), '.') for d in ('libcrypto-3-x64.dll', 'libssl-3-x64.dll') if os.path.exists(os.path.join(_dlls_dir, d))]
_ma = collect_all('mistralai')
_ff = collect_all('imageio_ffmpeg')
_nw = collect_all('num2words')
a = Analysis(
    [os.path.join(ROOT,'app','main.py')],
    pathex=[os.path.join(ROOT,'app')],
    binaries=_ssl_bins + _ma[1] + _ff[1] + _nw[1],
    datas=_ma[0] + _ff[0] + _nw[0] + [(os.path.join(ROOT,'assets','icon.ico'), 'assets')],
    hiddenimports=collect_submodules('engine') + collect_submodules('gui') + collect_submodules('mistralai') + collect_submodules('imageio_ffmpeg') + collect_submodules('num2words'),

    hookspath=[],
    runtime_hooks=[],
    excludes=['torch', 'transformers', 'numpy', 'scipy', 'librosa', 'soundfile'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name='StudioPodcastTTS',
    icon=os.path.join(ROOT,'assets','icon.ico'),
    console=False,
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe, a.binaries, a.datas,
    name='StudioPodcastTTS',
)
