"""AWS Step Functions trigger for post-upload validation."""

from __future__ import annotations

import json

import boto3
import structlog

logger = structlog.get_logger("step_functions")


class StepFunctionsTrigger:
    """Invokes a Step Functions state machine after successful upload."""

    def __init__(self, arn: str, region: str = "ca-central-1") -> None:
        self._arn = arn
        self._sfn = boto3.client("stepfunctions", region_name=region)

    def trigger(
        self,
        session_id: str,
        manifest_hash: str,
        uploaded_files: list[str],
        total_bytes: int,
    ) -> str:
        """Start a Step Functions execution for the uploaded session.

        Args:
            session_id: Unique session identifier.
            manifest_hash: Hash of the manifest for verification.
            uploaded_files: List of successfully uploaded relative paths.
            total_bytes: Total bytes uploaded.

        Returns:
            The execution ARN.
        """
        input_data = {
            "session_id": session_id,
            "manifest_hash": manifest_hash,
            "uploaded_files": uploaded_files,
            "total_files": len(uploaded_files),
            "total_bytes": total_bytes,
        }

        response = self._sfn.start_execution(
            stateMachineArn=self._arn,
            name=f"{session_id}-{manifest_hash[:8]}",
            input=json.dumps(input_data),
        )

        execution_arn = response["executionArn"]
        logger.info(
            "step_function_triggered",
            session_id=session_id,
            execution_arn=execution_arn,
        )
        return execution_arn
