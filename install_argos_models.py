"""Install the local Argos models used by PROMOT."""
from __future__ import annotations

import argostranslate.package


PAIRS = (('de', 'en'), ('cs', 'en'), ('en', 'pl'))


def main():
    argostranslate.package.update_package_index()
    available = {
        (package.from_code, package.to_code): package
        for package in argostranslate.package.get_available_packages()
    }
    installed = {
        (package.from_code, package.to_code)
        for package in argostranslate.package.get_installed_packages()
    }
    for pair in PAIRS:
        if pair in installed:
            continue
        package = available.get(pair)
        if package is None:
            raise RuntimeError(f'Brak modelu Argos {pair[0]}->{pair[1]}')
        print(f'Instaluję model Argos {pair[0]}->{pair[1]}', flush=True)
        argostranslate.package.install_from_path(package.download())
    print('Modele Argos są gotowe.', flush=True)


if __name__ == '__main__':
    main()
