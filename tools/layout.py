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

def validate_override(value):
    """Only these fields may come from the layout editor or runtime-data."""
    if type(value) is not dict or set(value) != {'modules', 'newsLimit'}:
        raise ValueError('Layout must contain modules and newsLimit only')
    modules = value['modules']
    if (type(modules) is not list or not modules or len(modules) > len(MODULES)
            or any(type(name) is not str or name not in MODULES for name in modules)
            or len(set(modules)) != len(modules)):
        raise ValueError('Modules must be unique supported names')
    if type(value['newsLimit']) is not int or not 1 <= value['newsLimit'] <= 8:
        raise ValueError('newsLimit must be an integer from 1 to 8')
    return value


def editable_layout(layout):
    return {key: layout[key] for key in ('modules', 'newsLimit')}


def load_layout(path='config.json', override_path=None):
    data = json.loads(Path(path).read_text(encoding='utf-8')) if Path(path).exists() else {}
    cfg = dict(data.get('display', {}))
    if override_path is not None:
        cfg.update(validate_override(json.loads(Path(override_path).read_text(encoding='utf-8'))))
    preset = cfg.get('preset', 'daily')
    if preset not in PRESETS:
        raise ValueError('Unknown display preset')
    modules = cfg.get('modules', PRESETS[preset])
    news_limit = cfg.get('newsLimit', 4)
    validate_override({'modules': modules, 'newsLimit': news_limit})
    width, height = cfg.get('width', 1072), cfg.get('height', 1448)
    if not all(isinstance(v, int) and 300 <= v <= 4096 for v in (width, height)):
        raise ValueError('Display dimensions must be integers from 300 to 4096')
    scale = cfg.get('fontScale', 1)
    if not isinstance(scale, (float, int)) or not 0.75 <= scale <= 1.5:
        raise ValueError('fontScale must be between 0.75 and 1.5')
    timezone = cfg.get('timezone')
    if timezone:
        ZoneInfo(timezone)
    unit = cfg.get('temperatureUnit', 'C')
    if unit not in ('C', 'F'):
        raise ValueError('temperatureUnit must be C or F')
    return dict(modules=modules, width=width, height=height, fontScale=scale,
                timezone=timezone, temperatureUnit=unit, preset=preset, newsLimit=news_limit)
