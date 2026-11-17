"""Read one daily and one weekly section through the local Obsidian app."""

from datetime import timedelta
import re
import subprocess


CLI = '/Applications/Obsidian.app/Contents/MacOS/obsidian-cli'
DAILY = ('今日推进', '今日待办')
WEEKLY = ('本周目标', '本周待办')
HEADING = re.compile(r'^(#{1,6})\s+(.+?)\s*#*\s*$')
BULLET = re.compile(r'^\s*(?:[-*+]\s+|\d+[.)]\s+)(?:\[([^\]])\](?:\s+|$))?(.*?)\s*$')
LINK = re.compile(r'!?\[\[([^\]|]+)(?:\|([^\]]+))?\]\]|!?\[([^\]]+)\]\([^)]+\)')


def extract_tasks(markdown, headings, *, plain=False):
    """Ignore adjacent sections, completed checkboxes, code and comments."""
    section = None
    fence = None
    comment = False
    tasks = []
    lines = markdown.splitlines()
    if lines and lines[0].strip() == '---':
        end = next((i for i, line in enumerate(lines[1:], 1) if line.strip() in ('---', '...')), None)
        lines = lines[end + 1:] if end is not None else []
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith(('~~~', '```')):
            marker = stripped[0]
            if fence == marker:
                fence = None
            elif fence is None:
                fence = marker
            continue
        if fence is not None:
            continue
        if '%%' in line:
            if line.count('%%') % 2:
                comment = not comment
            continue
        if comment:
            continue
        heading = HEADING.match(line)
        if heading:
            title = re.sub(r'\s*[（(].*$', '', heading[2].strip('*_ '))
            level = len(heading[1])
            if section is not None and level <= section:
                break
            if section is None and title in headings:
                section = level
            continue
        if section is None:
            continue
        item = BULLET.match(line)
        if not item or (item[1] not in (' ', None) or (item[1] is None and not plain)):
            continue
        text = LINK.sub(lambda m: m[3] or m[2] or m[1].rsplit('/', 1)[-1].split('#')[0], item[2])
        text = ' '.join(text.replace('**', '').replace('`', '').split())
        if text.strip('-—…[] ') and text not in tasks:
            tasks.append(text)
    return tasks


def week_following_saturday(day):
    saturday = day - timedelta(days=(day.weekday() - 5) % 7)
    # The Saturday template starts the period that the journal labels by Monday.
    year, week, _ = (saturday + timedelta(days=2)).isocalendar()
    return f'{year:04d}-W{week:02d}'


def obsidian(vault, *args):
    result = subprocess.run([CLI, f'vault={vault}', *args], capture_output=True,
                            encoding='utf-8', timeout=4, check=True)
    text = result.stdout
    if result.stderr or text.startswith(('Error:', 'Command line interface', 'The CLI is unable')):
        raise ValueError('Obsidian read unavailable')
    return text


def read_tasks(config, day, read=obsidian):
    result = {}
    for key, sections in (('daily', DAILY), ('weekly', WEEKLY)):
        try:
            vault = config['vault']
            if key == 'daily':
                path = config['dailyPath'].format(year=day.year, date=day.isoformat())
            else:
                week = week_following_saturday(day)
                folder = config['weeklyFolder'].format(year=week[:4])
                paths = read(vault, 'files', f'folder={folder}', 'ext=md').splitlines()
                pattern = re.compile(re.escape(folder + '/' + week) + r'(?:-[^/]+)?\.md')
                matches = [path for path in paths if pattern.fullmatch(path)]
                if not matches:
                    result[key] = []
                    continue
                if len(matches) != 1:
                    raise ValueError('Ambiguous weekly note')
                path = matches[0]
            result[key] = extract_tasks(read(vault, 'read', f'path={path}'),
                                        sections, plain=key == 'weekly')
        except (KeyError, OSError, ValueError, subprocess.SubprocessError):
            result[key] = None
    return result
