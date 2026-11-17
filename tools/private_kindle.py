"""Authenticated LAN image only. The editor and Obsidian files stay on localhost."""

from hmac import compare_digest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import IPv4Address, ip_network
import json
import re
import ssl
import threading
import time
from urllib.parse import urlsplit
from urllib.request import Request, build_opener

from private_todos import LOCAL_STATE, enabled, local_tasks, overlay
from report_codex_quota import NoRedirect


DEVICE = LOCAL_STATE / 'kindle.json'
MAX_IMAGE = 4 * 1024 * 1024
HOME_NETWORKS = tuple(map(ip_network, ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')))
PUBLIC_SITE = 'https://westimmigrant-tech.github.io/daypane/'


def public_screen(language):
    name = 'screen-en.png' if language == 'en' else 'screen.png'
    request = Request(PUBLIC_SITE + name + '?local=' + str(time.time_ns()),
                      headers={'Cache-Control': 'no-cache'})
    with build_opener(NoRedirect()).open(request, timeout=7) as response:
        png = response.read(MAX_IMAGE + 1)
    if not png.startswith(b'\x89PNG\r\n\x1a\n') or len(png) > MAX_IMAGE:
        raise ValueError('Invalid public image')
    return png


class PrivateImage(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(16)

    def log_message(self, *_args):
        pass

    def do_GET(self):
        url = urlsplit(self.path)
        auth = self.headers.get('Authorization', '').encode('latin-1')
        if url.path != '/private/board.png' or not compare_digest(auth, self.server.bearer):
            self.send_error(404)
            return
        if not enabled():
            self.send_error(503)
            return
        language = 'en' if url.query == 'lang=en' else 'zh'
        try:
            tasks = local_tasks()
            if all(row is None for row in tasks.values()):
                raise ValueError('No private source')
            png = overlay(public_screen(language), tasks, language)
        except (ValueError, OSError):
            self.send_error(503)
            return
        self.send_response(200)
        self.send_header('Content-Type', 'image/png')
        self.send_header('Content-Length', str(len(png)))
        self.send_header('Cache-Control', 'private, no-store')
        self.end_headers()
        self.wfile.write(png)


def start_private_kindle():
    if not DEVICE.is_file():
        return
    config = json.loads(DEVICE.read_text(encoding='utf-8'))
    address = IPv4Address(config['bind'])
    secret = config['token']
    if not any(address in network for network in HOME_NETWORKS) or not re.fullmatch('[a-f0-9]{64}', secret):
        raise ValueError('Invalid Kindle pairing configuration')
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(LOCAL_STATE / 'server.pem', LOCAL_STATE / 'server.key')
    server = ThreadingHTTPServer((str(address), 8789), PrivateImage)
    server.bearer = ('Bearer ' + secret).encode('ascii')
    server.socket = context.wrap_socket(server.socket, server_side=True, do_handshake_on_connect=False)
    threading.Thread(target=server.serve_forever, daemon=True).start()
