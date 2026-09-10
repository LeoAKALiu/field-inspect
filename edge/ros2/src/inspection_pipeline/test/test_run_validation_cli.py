"""Persist CLI failure reports; requires the ROS Humble rosbag2_py environment."""

import json

import pytest

from inspection_pipeline.run_validation import main


@pytest.mark.parametrize("previous_success", [False, True])
def test_failed_validation_writes_requested_report(tmp_path, capsys, previous_success):
    report_path = tmp_path / "run-validation.json"
    if previous_success:
        report_path.write_text('{"schema_version": "1.0", "valid": true}\n')

    result = main([str(tmp_path / "missing-run"), "--output", str(report_path)])

    assert result == 1
    printed = json.loads(capsys.readouterr().out)
    assert printed["valid"] is False
    assert printed["error"]
    assert json.loads(report_path.read_text()) == printed
