import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from http.server import ThreadingHTTPServer
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
from core import Research

class ServerTests(unittest.TestCase):
    def test_panel_authorization_and_validation(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(app, 'research', Research(tmp)):
            server = ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            root = f'http://127.0.0.1:{server.server_port}'
            try:
                self.assertEqual(requests.get(root, timeout=5).status_code, 200)
                self.assertEqual(requests.get(root+'/api/state', timeout=5).status_code, 403)
                headers = {'X-Panel-Token': app.TOKEN}
                self.assertEqual(requests.get(root+'/api/state', headers=headers, timeout=5).json()['running'], False)
                self.assertEqual(requests.get(root+'/api/xlsx', headers=headers, timeout=5).status_code, 400)
                self.assertEqual(requests.post(root+'/api/start', json=[], headers=headers, timeout=5).status_code, 400)
                self.assertEqual(requests.get(root, headers={'Host':'evil.example'}, timeout=5).status_code, 403)
            finally:
                server.shutdown()
                server.server_close()
                worker.join()

if __name__ == '__main__':
    unittest.main()
