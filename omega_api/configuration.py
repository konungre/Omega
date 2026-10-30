"""Operator-editable configuration schema, separate from credential storage."""
import json
from pathlib import Path
import re
from .store import require

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / 'config/dashboard-schema.json').read_text())


def validate(values):
    require(isinstance(values, dict) and bool(values) and set(values) <= set(SCHEMA),
            'Use only the documented non-secret Omega configuration keys')
    for key, value in values.items():
        spec = SCHEMA[key]
        valid = {'boolean': type(value) is bool, 'integer': type(value) is int,
                 'number': type(value) in (int, float), 'string': isinstance(value, str)}[spec['type']]
        require(valid, f'{key} must be {spec["type"]}')
        if 'minimum' in spec:
            require(spec['minimum'] <= value <= spec['maximum'], f'{key} must be between {spec["minimum"]} and {spec["maximum"]}')
        if isinstance(value, str):
            require(len(value) <= 2000 and '\x00' not in value, f'{key} is too long or contains a null character')
        if 'enum' in spec:
            require(value in spec['enum'], f'{key} must be one of {spec["enum"]}')
    return values


def effective():
    import sys
    config = sys.modules.get('config')
    return {key: config.config_get_by_key(key, spec['default']) if config else spec['default']
            for key, spec in SCHEMA.items()}


def locked():
    """Public fields fixed by explicit launch settings."""
    import os
    import sys
    config = sys.modules.get('config')
    command_line = getattr(config, '_COMMAND_LINE', {})
    return {key: 'launch argument' if key in command_line else 'environment'
            for key in SCHEMA if key in command_line or 'OMEGA_' + key in os.environ}
