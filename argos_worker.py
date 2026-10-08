"""Isolated Argos Translate worker; reads and writes one JSON batch."""
from __future__ import annotations

import json
import os
import sys

os.environ.setdefault('ARGOS_CHUNK_TYPE', 'MINISBD')

import argostranslate.translate


def main():
    items = json.load(sys.stdin)
    if not isinstance(items, list):
        raise ValueError('Niepoprawna paczka tłumaczeń')
    for item in items:
        text = str(item.get('text') or '')
        source = str(item.get('source') or '')
        translation = ''
        if text and source:
            try:
                translation = argostranslate.translate.translate(text, source, 'pl')
            except Exception:
                translation = ''
        sys.stdout.write(json.dumps({
            'id': str(item.get('id') or ''),
            'text': text,
            'source': source,
            'translation': translation,
        }, ensure_ascii=False) + '\n')
        sys.stdout.flush()


if __name__ == '__main__':
    main()
