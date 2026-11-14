"""Validated layout profiles, independent of device interface language."""
import json
from pathlib import Path
from zoneinfo import ZoneInfo

MODULES = ('news', 'ai', 'weather', 'market', 'clocks', 'mlb', 'quote')
PRESETS = {
    'daily': list(MODULES),
    'work': ['clocks', 'ai', 'news', 'weather', 'market'],
    'minimal': ['weather', 'clocks', 'quote'],
}

def load_layout(path='config.json'):
    data = json.loads(Path(path).read_text(encoding='utf-8')) if Path(path).exists() else {}
    cfg = data.get('display', {})
    preset = cfg.get('preset', 'daily')
    if preset not in PRESETS:
        raise ValueError('Unknown display preset')
    modules = cfg.get('modules', PRESETS[preset])
    if not isinstance(modules, list) or not modules or len(modules) != len(set(modules)) or any(m not in MODULES for m in modules):
        raise ValueError('Modules must be unique supported names')
    width, height = cfg.get('width', 1072), cfg.get('height', 1448)
    if not all(isinstance(v, int) and 300 <= v <= 4096 for v in (width, height)):
        raise ValueError('Display dimensions must be integers from 300 to 4096')
    scale = cfg.get('fontScale', 1)
    if not isinstance(scale, (float, int)) or not 0.75 <= scale <= 1.5:
        raise ValueError('fontScale must be between 0.75 and 1.5')
    news_limit = cfg.get('newsLimit', 4)
    if type(news_limit) is not int or not 1 <= news_limit <= 8:
        raise ValueError('newsLimit must be an integer from 1 to 8')
    timezone = cfg.get('timezone')
    if timezone:
        ZoneInfo(timezone)
    unit = cfg.get('temperatureUnit', 'C')
    if unit not in ('C', 'F'):
        raise ValueError('temperatureUnit must be C or F')
    return dict(modules=modules, width=width, height=height, fontScale=scale,
                timezone=timezone, temperatureUnit=unit, preset=preset, newsLimit=news_limit)
