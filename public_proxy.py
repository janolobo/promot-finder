"""Public localhost reverse proxy for a Cloudflare quick tunnel."""
from __future__ import annotations

import gzip
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen


ORIGIN = 'http://127.0.0.1:8765'


class Proxy(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def forward(self):
        length = int(self.headers.get('Content-Length', 0))
        if length < 0 or length > 1_000_000:
            return self.reply_error(413, 'Zbyt duże żądanie')
        body = self.rfile.read(length) if length else None
        headers = {'Host': '127.0.0.1:8765'}
        for name in ('Content-Type', 'X-Panel-Token', 'Accept'):
            if self.headers.get(name):
                headers[name] = self.headers[name]
        request = Request(ORIGIN + self.path, data=body, headers=headers, method=self.command)
        try:
            response = urlopen(request, timeout=180)
        except HTTPError as exc:
            response = exc
        except Exception:
            return self.reply_error(502, 'Panel lokalny jest chwilowo niedostępny')
        content = response.read()
        compressed = 'gzip' in self.headers.get('Accept-Encoding', '').lower() and len(content) > 1024
        if compressed:
            content = gzip.compress(content, compresslevel=5)
        self.send_response(response.status)
        for name in ('Content-Type', 'Content-Disposition', 'Cache-Control', 'X-Content-Type-Options', 'Referrer-Policy'):
            value = response.headers.get(name)
            if value:
                self.send_header(name, value)
        if compressed:
            self.send_header('Content-Encoding', 'gzip')
            self.send_header('Vary', 'Accept-Encoding')
        self.send_header('Content-Length', str(len(content)))
        self.send_header('X-Frame-Options', 'SAMEORIGIN')
        self.end_headers()
        try:
            self.wfile.write(content)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def reply_error(self, status, message):
        body = message.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'text/plain; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    do_GET = forward
    do_POST = forward


if __name__ == '__main__':
    print('PROMOT proxy: http://127.0.0.1:8766', flush=True)
    ThreadingHTTPServer(('127.0.0.1', 8766), Proxy).serve_forever()
