from __future__ import annotations

import json
import logging

from rednote_spider.observability import JsonFormatter, describe_database_target, log_database_target


def test_json_formatter_renders_json_with_extra_fields():
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="test.logger",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg="hello world",
        args=(),
        exc_info=None,
    )
    setattr(record, "event", "unit_test")
    setattr(record, "processed", 3)

    payload = json.loads(formatter.format(record))
    assert payload["message"] == "hello world"
    assert payload["logger"] == "test.logger"
    assert payload["event"] == "unit_test"
    assert payload["processed"] == 3


def test_json_formatter_keeps_unicode_text():
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="test.logger",
        level=logging.INFO,
        pathname=__file__,
        lineno=20,
        msg="中文内容",
        args=(),
        exc_info=None,
    )

    rendered = formatter.format(record)
    assert "中文内容" in rendered
    assert "\\u4e2d\\u6587" not in rendered


def test_describe_database_target_extracts_backend_and_name():
    payload = describe_database_target(
        "postgresql+psycopg://iot:secret@1.2.3.4:5432/rednote_spider"
    )
    assert payload["database_backend"] == "postgresql+psycopg"
    assert payload["database_name"] == "rednote_spider"
    assert payload["database_host"] == "1.2.3.4"


def test_log_database_target_emits_database_name(caplog):
    logger = logging.getLogger("test.database")

    with caplog.at_level(logging.INFO, logger=logger.name):
        log_database_target(logger, database_url="sqlite:///./rednote.db", source="unit_test")

    record = caplog.records[-1]
    assert record.getMessage() == "database_target"
    assert record.event == "database_target"
    assert record.source == "unit_test"
    assert record.database_backend == "sqlite"
    assert record.database_name == "rednote.db"
