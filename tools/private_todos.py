"""Compose local-only tasks onto a published or preview DayPane PNG."""

from datetime import datetime
from io import BytesIO
import json
from pathlib import Path
import tempfile
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageOps

from obsidian_todos import read_tasks
from render_screen import clip, f


LOCAL_STATE = Path.home() / 'Library/Application Support/DayPane/private-todos'
SETTINGS = LOCAL_STATE / 'settings.json'
SOURCE = LOCAL_STATE / 'obsidian-source.json'
TOROW = 38
LOCAL_ZONE = ZoneInfo('Asia/Shanghai')


def enabled():
    try:
        return json.loads(SETTINGS.read_text(encoding='utf-8')).get('enabled') is True
    except FileNotFoundError:
        return False


def store_enabled(value):
    LOCAL_STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', dir=LOCAL_STATE, encoding='utf-8', delete=False) as handle:
        tmp = Path(handle.name)
        json.dump({'enabled': value}, handle)
    try:
        tmp.replace(SETTINGS)
    finally:
        tmp.unlink(missing_ok=True)


def local_tasks():
    try:
        source = json.loads(SOURCE.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {'daily': None, 'weekly': None}
    return read_tasks(source, datetime.now(LOCAL_ZONE).date())


def overlay(png, tasks, language='zh'):
    with Image.open(BytesIO(png)) as source:
        img = source.convert('L')
    area = ImageOps.invert(img).getbbox()
    if not area:
        raise ValueError('Invalid base image')
    y = area[3] + 20
    if img.height - y < 154:
        raise ValueError('Layout exceeds screen height')
    draw = ImageDraw.Draw(img)
    left, right = 40, img.width - 40
    draw.line((left, y, right, y), fill=128, width=1)
    title = 'Obsidian 待办' if language == 'zh' else 'Obsidian tasks'
    draw.text((left, y + 9), title, font=f(36, bold=True), fill=0)
    y += 58
    caption_font = f(30, bold=True)
    task_font = f(29)
    columns = (
        ('daily', '今日' if language == 'zh' else 'Today'),
        ('weekly', '本周' if language == 'zh' else 'This week'),
    )
    step = (right - left) // 2
    for index, (name, label) in enumerate(columns):
        x = left + step * index
        draw.text((x, y), label, font=caption_font, fill=0)
        rows = tasks.get(name)
        if rows is None:
            rows = ['暂无法读取' if language == 'zh' else 'Unavailable']
        elif not rows:
            rows = ['暂无待办' if language == 'zh' else 'No open tasks']
        count = 2 if img.height - y >= 123 else 1
        for i, line in enumerate(rows[:count]):
            line = '· ' + line if tasks.get(name) else line
            draw.text((x, y + 37 + TOROW * i), clip(draw, line, task_font, step - 17),
                      font=task_font, fill=0 if tasks.get(name) else 100)
    out = BytesIO()
    img.convert('1', dither=Image.Dither.FLOYDSTEINBERG).save(out, 'PNG')
    return out.getvalue()
