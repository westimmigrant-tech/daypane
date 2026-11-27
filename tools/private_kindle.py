"""Serve the paired Kindle an encrypted personal board with AIHOT headlines."""

from concurrent.futures import ThreadPoolExecutor
from hmac import compare_digest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from ipaddress import IPv4Address, ip_network
import json
from pathlib import Path
import re
import ssl
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener

from PIL import Image, ImageDraw, ImageOps

from render_screen import clip, f
from report_codex_quota import NoRedirect


LOCAL_STATE = Path.home() / 'Library/Application Support/DayPane/private-board'
DEVICE = LOCAL_STATE / 'kindle.json'
MAX_IMAGE = 4 * 1024 * 1024
HOME_NETWORKS = tuple(map(ip_network, ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')))
PUBLIC_SITE = 'https://westimmigrant-tech.github.io/daypane/'
AIHOT = 'https://aihot.news/api/v1/hot-topics'
_aihot_lock = threading.Lock()
_checked = 0.0
_etag = None
_topics = []
_aihot_available = False


def aihot_topics():
    global _checked, _etag, _topics, _aihot_available
    with _aihot_lock:
        if _checked and time.monotonic() - _checked < 600:
            return _topics if _aihot_available else []
        _checked = time.monotonic()
        headers = {'Accept': 'application/json', 'User-Agent': 'DayPane/1.0'}
        if _etag:
            headers['If-None-Match'] = _etag
        try:
            with build_opener(NoRedirect()).open(Request(AIHOT, headers=headers), timeout=4) as response:
                result = json.loads(response.read(131_072))
                items = result['items']
                _topics = [item for item in items if isinstance(item.get('title'), str) and item['title'].strip()][:2]
                _etag = response.headers.get('ETag')
                _aihot_available = True
        except HTTPError as error:
            _aihot_available = error.code == 304
        except (OSError, ValueError, TypeError, AttributeError, KeyError):
            _aihot_available = False
        return _topics if _aihot_available else []


def add_ai_news(png, topics):
    if not topics:
        return png
    with Image.open(BytesIO(png)) as source:
        if source.size != (1072, 1448):
            return png
        img = source.convert('L')
    bounds = ImageOps.invert(img).getbbox()
    if not bounds:
        return png
    y = bounds[3] + 18
    if img.height - y < 143:
        return png
    draw = ImageDraw.Draw(img)
    draw.line((40, y, 1032, y), fill=128)
    draw.text((40, y + 9), 'AI 热点 · AIHOT', font=f(36, bold=True), fill=0)
    font = f(32)
    for i, item in enumerate(topics):
        title = ' '.join(item['title'].split())
        draw.text((40, y + 52 + i * 37), clip(draw, '· ' + title, font, 992), font=font, fill=0)
    out = BytesIO()
    img.convert('1', dither=Image.Dither.FLOYDSTEINBERG).save(out, 'PNG')
    return out.getvalue()


def public_screen(language):
    name = 'screen-en.png' if language == 'en' else 'screen.png'
    request = Request(PUBLIC_SITE + name + '?local=' + str(time.time_ns()),
                      headers={'Cache-Control': 'no-cache'})
    with build_opener(NoRedirect()).open(request, timeout=6) as response:
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
        language = 'en' if url.query == 'lang=en' else 'zh'
        try:
            if language == 'zh':
                with ThreadPoolExecutor(max_workers=1) as pool:
                    topics = pool.submit(aihot_topics)
                    png = add_ai_news(public_screen(language), topics.result())
            else:
                png = public_screen(language)
        except (OSError, ValueError):
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
