"""Shared base class for OpenClaw skills."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import yaml


class SkillBase(ABC):
    """Base interface and utilities for all skills."""

    def __init__(self, config_path: str | Path | None = None) -> None:
        """Initialize the skill and optionally load configuration."""
        self.logger = self.get_logger()
        self.config: dict[str, Any] = {}

        if config_path is not None:
            self.config = self.load_config(config_path)

    @abstractmethod
    def run(self) -> Any:
        """Execute the skill."""

    def get_logger(self) -> logging.Logger:
        """Return a logger scoped to the current skill class."""
        return logging.getLogger(self.__class__.__name__)

    def load_config(self, config_path: str | Path) -> dict[str, Any]:
        """Load YAML configuration from disk."""
        path = Path(config_path)

        with path.open("r", encoding="utf-8") as config_file:
            data = yaml.safe_load(config_file) or {}

        if not isinstance(data, dict):
            msg = f"Expected mapping in config file: {path}"
            raise ValueError(msg)

        return data
