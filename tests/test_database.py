from __future__ import annotations

import builtins
import importlib
import sys


def test_database_module_falls_back_to_env_when_config_import_fails(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./fallback.db")
    sys.modules.pop("rednote_spider.database", None)
    sys.modules.pop("rednote_spider.config", None)

    original_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "rednote_spider.config":
            raise ModuleNotFoundError("No module named 'pydantic_settings'")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    module = importlib.import_module("rednote_spider.database")
    engine = module.make_engine()

    assert str(engine.url) == "sqlite:///./fallback.db"
