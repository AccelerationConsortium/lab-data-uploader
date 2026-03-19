"""Tests for the StepFunctionsTrigger."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agent.step_functions import StepFunctionsTrigger


@pytest.fixture()
def mock_sfn():
    with patch("agent.step_functions.boto3") as mock_boto3:
        mock_client = MagicMock()
        mock_boto3.client.return_value = mock_client
        yield mock_client


@pytest.fixture()
def trigger(mock_sfn) -> StepFunctionsTrigger:
    return StepFunctionsTrigger(
        arn="arn:aws:states:ca-central-1:123456789:stateMachine:TestMachine",
        region="ca-central-1",
    )


class TestTrigger:
    def test_trigger_calls_start_execution(
        self, trigger: StepFunctionsTrigger, mock_sfn
    ) -> None:
        mock_sfn.start_execution.return_value = {
            "executionArn": "arn:aws:states:ca-central-1:123456789:execution:TestMachine:ses-001-abcd1234"
        }

        arn = trigger.trigger(
            session_id="ses-001",
            manifest_hash="abcd1234efgh5678",
            uploaded_files=["data.csv", "log.txt"],
            total_bytes=1024,
        )

        assert arn.startswith("arn:aws:states:")
        mock_sfn.start_execution.assert_called_once()

        call_kwargs = mock_sfn.start_execution.call_args.kwargs
        assert call_kwargs["stateMachineArn"].endswith("TestMachine")
        assert call_kwargs["name"] == "ses-001-abcd1234"

    def test_trigger_passes_correct_input(
        self, trigger: StepFunctionsTrigger, mock_sfn
    ) -> None:
        import json

        mock_sfn.start_execution.return_value = {
            "executionArn": "arn:aws:states:ca-central-1:123456789:execution:TestMachine:x"
        }

        trigger.trigger(
            session_id="ses-002",
            manifest_hash="hash1234",
            uploaded_files=["a.csv"],
            total_bytes=500,
        )

        call_kwargs = mock_sfn.start_execution.call_args.kwargs
        input_data = json.loads(call_kwargs["input"])
        assert input_data["session_id"] == "ses-002"
        assert input_data["manifest_hash"] == "hash1234"
        assert input_data["uploaded_files"] == ["a.csv"]
        assert input_data["total_files"] == 1
        assert input_data["total_bytes"] == 500
