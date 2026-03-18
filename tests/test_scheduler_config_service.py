from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from rednote_spider.models import Base
from rednote_spider.services.scheduler_config_service import SchedulerConfigService


def _session_factory(tmp_path: Path):
    db_path = tmp_path / "scheduler_config.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, class_=Session, autoflush=False, autocommit=False)


def test_scheduler_config_service_bootstraps_default_modes(tmp_path: Path):
    sf = _session_factory(tmp_path)
    service = SchedulerConfigService(sf)

    rows = service.list_configs()

    assert [row.mode for row in rows] == ["discover"]
    assert rows[0].enabled is True
    assert rows[0].loop_interval_seconds == 900
    assert rows[0].note_limit == 20


def test_scheduler_config_service_updates_enabled_and_interval(tmp_path: Path):
    sf = _session_factory(tmp_path)
    service = SchedulerConfigService(sf)

    updated = service.set_config("discover", enabled=False, loop_interval_seconds=123, note_limit=7)
    fetched = service.get_config("discover")

    assert updated.mode == "discover"
    assert updated.enabled is False
    assert updated.loop_interval_seconds == 123
    assert updated.note_limit == 7
    assert fetched.enabled is False
    assert fetched.loop_interval_seconds == 123
    assert fetched.note_limit == 7


def test_scheduler_config_service_rejects_invalid_inputs(tmp_path: Path):
    sf = _session_factory(tmp_path)
    service = SchedulerConfigService(sf)

    try:
        service.get_config("unknown")
        raise AssertionError("unknown mode should fail")
    except ValueError as exc:
        assert "unsupported mode" in str(exc)

    try:
        service.set_config("discover", enabled=True, loop_interval_seconds=0)
        raise AssertionError("non-positive interval should fail")
    except ValueError as exc:
        assert "loop_interval_seconds must be >= 1" in str(exc)

    try:
        service.set_config("opportunity", enabled=True, loop_interval_seconds=60)
        raise AssertionError("unsupported mode should fail")
    except ValueError as exc:
        assert "unsupported mode" in str(exc)

    try:
        service.set_config("discover", enabled=True, loop_interval_seconds=60, note_limit=0)
        raise AssertionError("non-positive note limit should fail")
    except ValueError as exc:
        assert "note_limit must be >= 1" in str(exc)
