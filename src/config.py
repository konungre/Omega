import os
import json
import sys
import logging
import pathlib
import re
import yaml

logger = logging.getLogger(__name__)

_REPO = pathlib.Path(__file__).parent.parent.resolve()
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
_CONFIG = {}
_COMMAND_LINE = {}
_CONFIG_FILE = {}

_INTEGER_LITERAL = re.compile(r"^[+-]?(?:0|[1-9][0-9]*)$")
_FLOAT_LITERAL = re.compile(
    r"^[+-]?(?!0[0-9])(?:(?:[0-9]+\.[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?|[0-9]+[eE][+-]?[0-9]+)$"
)

def init_config(command_line):
    """Initialize configuration: drop configuration cache and load
    configuration file"""
    global _CONFIG, _COMMAND_LINE, _CONFIG_FILE
    _CONFIG = {}
    _COMMAND_LINE = command_line_to_dict(command_line)
    _CONFIG_FILE = {}
    default_config_path = _REPO.joinpath("config", "config.yaml")
    config_path = config_get_by_key("config", default_config_path)
    try:
        logger.info(f"Loading configuration from {config_path}")
        with open(config_path, "r") as f:
            _CONFIG_FILE = yaml.safe_load(f)
        if not _CONFIG_FILE:
            _CONFIG_FILE = {}
    except:
        logger.exception(f"Could not load configuration file {config_path}")
    memory = _COMMAND_LINE.get("memoryDirectory", os.environ.get("OMEGA_memoryDirectory", _CONFIG_FILE.get("memoryDirectory", str(_REPO / "memory"))))
    overrides = pathlib.Path(str(memory)) / "operator-config.json"
    if overrides.exists():
        with overrides.open() as source:
            values = json.load(source)
        from omega_api.configuration import validate
        if values:
            _CONFIG_FILE.update(validate(values))


def apply_operator_config(values):
    """Apply persisted overrides on the agent thread; CLI/environment still win."""
    from omega_api.configuration import SCHEMA, validate
    validate(values)
    memory = pathlib.Path(str(config_get_by_key("memoryDirectory", str(_REPO / "memory"))))
    path = memory / "operator-config.json"
    previous = json.loads(path.read_text()) if path.exists() else {}
    previous.update(values)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(previous, indent=2) + "\n")
    temporary.replace(path)
    _CONFIG_FILE.update(values)
    for key in values:
        _CONFIG.pop(key, None)
    return [[key, config_get_by_key(key, SCHEMA[key]['default'])] for key in values if SCHEMA[key]['live']]

def config_get_by_key(key, default=None):
    """Get configuration parameter from the list of sources: (1) command line
    parameters, (2) environment variable with OMEGA_$key name, (3)
    configuration file, (4) use $default value."""
    global _CONFIG, _COMMAND_LINE, _CONFIG_FILE
    if key in _COMMAND_LINE:
        value = _COMMAND_LINE[key]
        expected = default if default is not None else _CONFIG_FILE.get(key)
        if type(expected) in (int, float):
            if value is True:
                raise ValueError(
                    f"{key} requires a numeric value; use {key}=<number>"
                )
            value = _command_line_value(value)
        # A later caller can supply a type that an earlier lookup did not know.
        if key in _CONFIG and type(_CONFIG[key]) is type(value) and _CONFIG[key] == value:
            return _CONFIG[key]
        return _cache_config(key, value, "command line")
    if key in _CONFIG:
        return _CONFIG.get(key)
    envkey = f"OMEGA_{key}"
    if envkey in os.environ:
        return _cache_config(key, os.environ.get(envkey), "environment variable")
    if key in _CONFIG_FILE:
        return _cache_config(key, _CONFIG_FILE.get(key), "config file")
    return _cache_config(key, default, "defaults")

def _cache_config(key, value, source):
    global _CONFIG
    _CONFIG[key] = value
    logger.info(f"Configuration item resolved using {source}: {key}={value}")
    return value

def _command_line_value(value):
    """Parse a CLI value for a setting known to be numeric."""
    if _INTEGER_LITERAL.fullmatch(value):
        return int(value)
    if _FLOAT_LITERAL.fullmatch(value):
        return float(value)
    return value


def command_line_to_dict(list):
    """Converts list of <key>=<value> pairs into Python dictionary. If
    parameter doesn't include "=" it is added as a boolean value
    <parameter>=True"""
    dict = {}
    for arg in list:
        kv = arg.split("=", 1)
        if len(kv) == 2:
            dict[kv[0]] = kv[1]
        else:
            dict[kv[0]] = True
    return dict


def apply_operator_json(raw):
    return apply_operator_config(json.loads(str(raw)))
