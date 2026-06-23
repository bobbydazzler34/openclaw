"""FareFynder synthetic auth monitor entry point."""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

import yaml

from openclaw.skills.farefynder_monitor.checker import CheckResult, run_auth_check
from openclaw.skills.farefynder_monitor.reporter import (
    record_result_safe,
    send_failure_alert_safe,
)

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"


def _load_config(config_path: Path) -> dict:
    with config_path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


async def _run_async(config_path: Path | None = None) -> int:
    check_name = "synthetic_login"
    timeout_seconds = 10.0

    try:
        path = config_path or DEFAULT_CONFIG_PATH
        config = _load_config(path)
        check_name = config.get("check_name", check_name)
        timeout_seconds = float(config.get("http_timeout_seconds", timeout_seconds))

        try:
            result = await run_auth_check(timeout_seconds=timeout_seconds)
        except Exception as exc:
            result = CheckResult(
                status="fail",
                latency_ms=None,
                error_detail=str(exc),
                raw_response=None,
            )
    except Exception as exc:
        result = CheckResult(
            status="fail",
            latency_ms=None,
            error_detail=str(exc),
            raw_response=None,
        )

    if result.status == "pass":
        await record_result_safe(
            check_name=check_name,
            result=result,
            timeout_seconds=timeout_seconds,
        )
        return 0

    await record_result_safe(
        check_name=check_name,
        result=result,
        timeout_seconds=timeout_seconds,
    )
    await send_failure_alert_safe(
        check_name=check_name,
        result=result,
        timeout_seconds=timeout_seconds,
    )
    return 0


def main(config_path: Path | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    return asyncio.run(_run_async(config_path))


if __name__ == "__main__":
    sys.exit(main())
