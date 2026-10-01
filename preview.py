"""Local homepage thumbnails for firms already on the list."""
from __future__ import annotations
import hashlib
import shutil
import subprocess
import threading
from pathlib import Path
from xml.sax.saxutils import escape

from core import safe_url

_LOCK = threading.Lock()
_BROWSERS = (
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/Applications/Chromium.app/Contents/MacOS/Chromium',
    '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
)


def browser_path():
    for candidate in _BROWSERS:
        if Path(candidate).is_file():
            return candidate
    return shutil.which('google-chrome') or shutil.which('chromium') or ''


def placeholder(name):
    label = escape((name or 'Brak strony')[:80])
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="800"><rect width="100%" height="100%" fill="#e7eef0"/><text x="48" y="390" font-family="sans-serif" font-size="42" fill="#153c47">{label}</text></svg>'
    return svg.encode('utf-8'), 'image/svg+xml'


def thumbnail(url, cache, browser=''):
    safe_url(url)
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    dest = cache / (hashlib.sha256(url.encode('utf-8')).hexdigest() + '.png')
    if dest.is_file() and dest.stat().st_size > 800:
        return dest.read_bytes(), 'image/png'
    binary = browser or browser_path()
    if not binary:
        raise FileNotFoundError('Brak przeglądarki do miniatury')
    with _LOCK:
        if dest.is_file() and dest.stat().st_size > 800:
            return dest.read_bytes(), 'image/png'
        profile = cache / 'chrome-profile'
        profile.mkdir(exist_ok=True)
        subprocess.run(
            [binary, '--headless=new', '--disable-gpu', '--hide-scrollbars', '--no-first-run', '--disable-extensions',
             '--window-size=1200,900', f'--user-data-dir={profile}', f'--screenshot={dest}', url],
            cwd=cache, timeout=25, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        dropped = cache / 'screenshot.png'
        if dropped.is_file() and not dest.is_file():
            dropped.replace(dest)
    if dest.is_file() and dest.stat().st_size > 800:
        return dest.read_bytes(), 'image/png'
    raise RuntimeError('Nie udało się zrobić miniatury')
