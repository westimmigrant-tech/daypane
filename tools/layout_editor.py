#!/usr/bin/env python3
"""Loopback-only editor for the DayPane layout published on runtime-data."""

import argparse
import base64
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
import logging
from pathlib import Path
import re
import threading
from urllib.error import HTTPError
from urllib.request import Request, build_opener
from zoneinfo import ZoneInfo

from layout import MODULES, editable_layout, load_layout, validate_override
from private_kindle import start_private_kindle
from report_codex_quota import BRANCH, NoRedirect, REPORT_URL, read_token
from render_screen import check_fonts, render


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'web' / 'editor'
CONFIG = ROOT / 'config.json'
LAYOUT_URL = REPORT_URL.removesuffix('quotas.json') + 'layout.json'
RENDER_LOCK = threading.Lock()
ASSETS = {
    '/': ('index.html', 'text/html; charset=utf-8'),
    '/editor.css': ('editor.css', 'text/css; charset=utf-8'),
    '/editor.js': ('editor.js', 'text/javascript; charset=utf-8'),
}


class RevisionChanged(Exception):
    pass


def github_request(payload=None):
    headers = {
        'Authorization': 'Bearer ' + read_token(),
        'Accept': 'application/vnd.github+json',
        'Content-Type': 'application/json',
        'User-Agent': 'DayPane-Layout',
        'X-GitHub-Api-Version': '2022-11-28',
    }
    request = Request(LAYOUT_URL + ('?ref=' + BRANCH if payload is None else ''),
                      headers=headers, method='GET' if payload is None else 'PUT',
                      data=json.dumps(payload).encode() if payload is not None else None)
    with build_opener(NoRedirect()).open(request, timeout=10) as response:
        return json.loads(response.read(65_536))


def saved_layout(default):
    try:
        result = github_request()
    except HTTPError as error:
        if error.code != 404:
            raise
        return default, None
    layout = json.loads(base64.b64decode(result['content']))
    return validate_override(layout), result['sha']


def save_layout(layout, revision):
    default = editable_layout(load_layout(CONFIG))
    for attempt in range(2):
        current, sha = saved_layout(default)
        if current == layout:
            return sha
        if revision != sha:
            raise RevisionChanged
        body = {
            'message': 'Update DayPane layout',
            'branch': BRANCH,
            'content': base64.b64encode((json.dumps(layout, indent=2) + '\n').encode()).decode(),
        }
        if sha:
            body['sha'] = sha
        try:
            return github_request(body)['content']['sha']
        except HTTPError as error:
            if error.code != 409 or attempt == 1:
                raise


def sample_data(now):
    # Sample content only. The editor never reads the local dashboard or personal data.
    stamp = int(now.timestamp())
    fetched = {'ok': True, 'fetchedAt': stamp * 1000}
    headlines = [
        '多国公布新一轮能源合作计划',
        '全球主要城市加强极端天气应对',
        '多地新增周末公共文化活动',
        '行动计划支持城市公共交通更新',
        '研究团队发布用于长文档理解的新模型',
        '开源社区更新模型评测工具',
        '电影节公布本届主竞赛入围片单',
        '多部新片确认全国上映日期',
    ]
    config = json.loads(CONFIG.read_text(encoding='utf-8'))
    prices = [3386.5, 1259.2, 10084.0, 3900.5, 22226.9, 50182.9, 680.2, 607.1, 8.581, 1.118]
    stocks = [
        {'ok': True, 'sym': item['sym'], 'label': item['name'], 'labelEn': item.get('nameEn'),
         'price': prices[i % len(prices)], 'changePct': 0.6 if i % 2 == 0 else -1.1,
         'priceDecimals': item.get('priceDecimals')}
        for i, item in enumerate(config.get('stocks', []))
    ]
    clocks = []
    for item in config.get('clocks', []):
        local = now.astimezone(ZoneInfo(item['tz']))
        clocks.append({**item, 'time': local.strftime('%H:%M'),
                       'date': local.strftime('%m/%d 周') + '一二三四五六日'[local.weekday()]})
    return {
        'news': {**fetched, 'source': '今日热榜', 'items': [{'title': headline} for headline in headlines]},
        'newsEn': {**fetched, 'items': [{'title': title} for title in (
            'Countries announce a clean energy partnership', 'Cities prepare for extreme weather',
            'Film festival announces competition lineup', 'Researchers release a new language model')]},
        'weather': {**fetched, 'city': config.get('weather', {}).get('city', '北京'),
                    'code': 2, 'text': '少云', 'temp': 6.9, 'high': 12.8, 'low': -3.7, 'humidity': 81},
        'stocks': {**fetched, 'items': stocks},
        'clocks': {'items': clocks},
        'mlb': {**fetched, 'items': [{'cn': '道奇', 'abbr': 'LAD'}, {'cn': '教士', 'abbr': 'SD'}]},
        'quotas': {**fetched, 'codex': {'ok': True, 'windows': {
            'fiveHour': {'usedPercent': 14, 'resetsAt': stamp + 10_800},
            'weekly': {'usedPercent': 37, 'resetsAt': stamp + 345_600},
        }}},
    }


def preview(override, language='zh'):
    layout = {**load_layout(CONFIG), **validate_override(override)}
    zone = ZoneInfo(layout['timezone']) if layout['timezone'] else None
    data = sample_data(datetime.now(zone))
    png = BytesIO()
    # The renderer uses process globals for the current canvas and language.
    with RENDER_LOCK:
        render(data, png, language, layout=layout)
    return png.getvalue()


class Editor(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def write(self, status, content, mime='application/json; charset=utf-8'):
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(content)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy',
                         "default-src 'none'; script-src 'self'; style-src 'self'; "
                         "img-src blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(content)

    def json(self, status, value):
        self.write(status, json.dumps(value, ensure_ascii=False).encode())

    def allowed_host(self):
        port = self.server.server_port
        return self.headers.get_all('Host') in ([f'127.0.0.1:{port}'], [f'localhost:{port}'])

    def do_GET(self):
        if not self.allowed_host():
            return self.json(403, {'error': '请从这台 Mac 打开编辑器。'})
        if self.path in ASSETS:
            file, mime = ASSETS[self.path]
            return self.write(200, (WEB / file).read_bytes(), mime)
        if self.path != '/api/layout':
            return self.json(404, {'error': '页面不存在。'})
        default = editable_layout(load_layout(CONFIG))
        try:
            layout, revision = saved_layout(default)
            state = {'layout': layout, 'revision': revision, 'connected': True}
        except (OSError, ValueError, KeyError, TypeError):
            state = {'layout': default, 'revision': None, 'connected': False}
        self.json(200, {'default': default, 'modules': MODULES, **state})

    def do_POST(self):
        if not self.allowed_host() or self.headers.get('Origin') != 'http://' + self.headers['Host']:
            return self.json(403, {'error': '请从编辑器页面操作。'})
        if self.path not in ('/api/preview', '/api/save'):
            return self.json(404, {'error': '页面不存在。'})
        if self.headers.get_content_type() != 'application/json':
            return self.json(415, {'error': '请求格式不正确。'})
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 4096 or self.headers.get('Transfer-Encoding'):
                raise ValueError
            payload = json.loads(self.rfile.read(length))
            if type(payload) is not dict or set(payload) != {'layout', 'revision'}:
                raise ValueError
            layout = validate_override(payload.get('layout'))
            revision = payload.get('revision')
            if revision is not None and (type(revision) is not str or not re.fullmatch('[0-9a-f]{40}', revision)):
                raise ValueError
        except (ValueError, OSError):
            return self.json(400, {'error': '请至少保留一个模块，新闻条数设为 1 到 8。'})
        try:
            png = preview(layout)
            if self.path == '/api/save':
                preview(layout, 'en')
        except OSError:
            logging.exception('DayPane failed to render preview')
            return self.json(500, {'error': '本机出图失败，请重新打开编辑器后重试。'})
        except ValueError as error:
            if str(error).startswith('Layout exceeds'):
                return self.json(422, {'error': '这一版放不进 Kindle。请减少新闻条数或隐藏一个模块。'})
            return self.json(422, {'error': '这份版式无法生成图片，请调整后重试。'})
        if self.path == '/api/preview':
            return self.write(200, png, 'image/png')
        try:
            revision = save_layout(layout, revision)
        except RevisionChanged:
            return self.json(409, {'error': '版式已在另一窗口更新，请重新打开编辑器。'})
        except HTTPError as error:
            return self.json(502, {'error': f'GitHub 拒绝保存（{error.code}），请稍后重试。'})
        except (OSError, ValueError, KeyError):
            return self.json(502, {'error': '无法保存到 GitHub，请检查网络后重试。'})
        self.json(200, {'revision': revision})

    def log_message(self, _format, *_args):
        # The service does not need to log local browser requests.
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8788)
    args = parser.parse_args()
    ok, details = check_fonts()
    if not ok:
        parser.exit(1, '\n'.join(details) + '\n')
    with ThreadingHTTPServer(('127.0.0.1', args.port), Editor) as server:
        try:
            start_private_kindle()
        except (OSError, ValueError, KeyError):
            logging.error('DayPane private Kindle connection unavailable')
        print(f'DayPane editor: http://127.0.0.1:{server.server_port}', flush=True)
        server.serve_forever()


if __name__ == '__main__':
    main()
