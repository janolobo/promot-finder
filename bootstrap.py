"""Install in a project-local virtualenv, recover from interrupted installation."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import venv

root = Path(__file__).resolve().parent
os.chdir(root)
if sys.version_info < (3, 11):
    raise SystemExit('Wymagany Python 3.11 lub nowszy.')
environment = root / '.venv'
python = environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
if not python.exists():
    print('Przygotowuję środowisko programu…', flush=True)
    venv.create(environment, with_pip=True)
check = subprocess.run([str(python), '-c', 'import requests, bs4, openpyxl, pypdf, ddgs, osmium, shapely, reportlab'], capture_output=True)
if check.returncode:
    subprocess.check_call([str(python), '-m', 'pip', 'install', '-r', str(root/'requirements-core.txt')])
desktop = subprocess.run([str(python), '-c', 'import webview'], capture_output=True)
attempted = environment / '.desktop-attempted'
if desktop.returncode and not attempted.exists():
    print('Instaluję obsługę osobnego okna. W razie problemu panel otworzy się w przeglądarce.', flush=True)
    result = subprocess.run([str(python), '-m', 'pip', 'install', 'pywebview>=5,<7'])
    attempted.touch()
    if result.returncode:
        print('Okno systemowe niedostępne; użyję przeglądarki.', flush=True)
raise SystemExit(subprocess.call([str(python), str(root/'app.py'), *sys.argv[1:]]))
