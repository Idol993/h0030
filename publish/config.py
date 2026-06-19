import os
from pathlib import Path
from typing import Dict, Optional

import yaml

from .models import PlatformConfig


CONFIG_DIR = Path.home() / ".publish"
CONFIG_FILE = CONFIG_DIR / "config.yaml"


def ensure_config_dir() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def load_config() -> Dict[str, PlatformConfig]:
    if not CONFIG_FILE.exists():
        return {}
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    configs = {}
    for name, cfg in data.items():
        configs[name] = PlatformConfig(name=name, **cfg)
    return configs


def save_config(configs: Dict[str, PlatformConfig]) -> None:
    ensure_config_dir()
    data = {}
    for name, cfg in configs.items():
        data[name] = {
            "type": cfg.type,
            "api_key": cfg.api_key,
            "api_secret": cfg.api_secret,
            "access_token": cfg.access_token,
            "params": cfg.params,
        }
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False)


def add_platform(config: PlatformConfig) -> None:
    configs = load_config()
    configs[config.name] = config
    save_config(configs)


def get_platform(name: str) -> Optional[PlatformConfig]:
    configs = load_config()
    return configs.get(name)


def remove_platform(name: str) -> bool:
    configs = load_config()
    if name in configs:
        del configs[name]
        save_config(configs)
        return True
    return False


def list_platforms() -> Dict[str, PlatformConfig]:
    return load_config()
