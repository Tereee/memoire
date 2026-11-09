"""Amorce d'exécution des tests du protocole B, dans le bac à sable.

Usage : python -I -B bootstrap.py <racine_import> <dossier_tests> <marqueur>

Copiée dans l'image Docker (/opt/sandbox/bootstrap.py) et utilisée telle quelle
par le mode local des tests hors ligne. Coupe le réseau au niveau Python (en plus
de --network none côté conteneur), lance unittest discover, puis imprime sur
une dernière ligne le marqueur suivi d'un résumé JSON.
"""
import json
import socket
import sys
import unittest


def _blocked(*args, **kwargs):
    raise OSError("network access is disabled in this sandbox")


for _name in ("socket", "create_connection", "getaddrinfo", "socketpair", "fromfd", "create_server"):
    if hasattr(socket, _name):
        setattr(socket, _name, _blocked)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

root, start, marker = sys.argv[1], sys.argv[2], sys.argv[3]
suite = unittest.defaultTestLoader.discover(start, top_level_dir=root)
result = unittest.TextTestRunner(stream=sys.stdout, verbosity=2).run(suite)
sys.stdout.flush()
print(marker + json.dumps({
    "tests_run": result.testsRun,
    "failures": sorted(t.id() for t, _ in result.failures),
    "errors": sorted(t.id() for t, _ in result.errors),
    "skipped": len(result.skipped),
    "ok": result.wasSuccessful(),
}))
