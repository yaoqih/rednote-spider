from __future__ import annotations

import json
import sys
from pathlib import Path

from rednote_spider.command_template_runner import run_command_template_json


def test_run_command_template_json_forwards_stderr_on_success(tmp_path: Path, capsys):
    script = tmp_path / 'emit_json_and_stderr.py'
    script.write_text(
        '\n'.join(
            [
                'import json, sys',
                "print('scan qr in terminal', file=sys.stderr, flush=True)",
                "print(json.dumps({'notes': []}, ensure_ascii=False))",
            ]
        ),
        encoding='utf-8',
    )

    payload = run_command_template_json(
        command_template=f'{sys.executable} {script} "{{keywords}}" {{max_notes}}',
        keywords='通勤',
        max_notes=2,
        error_prefix='discover command failed',
        timeout_seconds=5,
    )

    captured = capsys.readouterr()
    assert payload == {'notes': []}
    assert 'scan qr in terminal' in captured.err
