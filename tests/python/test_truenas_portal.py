"""Exercise the portal contract through real Compose interpolation, without Docker I/O."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

COMPOSE = Path(__file__).resolve().parents[2] / "deploy" / "truenas-collector.compose.yml"


@pytest.mark.skipif(shutil.which("docker") is None, reason="Docker Compose CLI unavailable")
def test_materialized_portal_is_integer_and_matches_the_fixed_lan_publication(
    tmp_path: Path,
) -> None:
    config = tmp_path / "empty-docker-config"
    config.mkdir(mode=0o700)
    digest = "sha256:" + "a" * 64
    environment = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "DOCKER_CONFIG": str(config),
        "COLLECTOR_IMAGE": "ghcr.io/example/collector@" + digest,
        "COLLECTOR_IMAGE_DIGEST": digest,
        "COLLECTOR_SOURCE_COMMIT": "b" * 40,
        "COLLECTOR_CAMPAIGN": "portal-contract",
        "COLLECTOR_NATIVE_SPOOL": "/mnt/fixture/hyperliquid-runtime/spool",
        "COLLECTOR_NATIVE_CONTROL": "/mnt/fixture/hyperliquid-runtime/control",
        "COLLECTOR_NATIVE_CAPTURE": "/mnt/fixture/hyperliquid-work/market-data/capture",
        "COLLECTOR_NATIVE_ARCHIVE": "/mnt/fixture/hyperliquid-archive/market-data",
        "COLLECTOR_STATUS_BIND_IP": "192.0.2.10",
        # A legacy setting cannot change only one side of the portal contract.
        "COLLECTOR_STATUS_PORT": "19999",
    }
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            "/dev/null",
            "--project-directory",
            str(tmp_path),
            "-p",
            "hyperliquid-portal-contract",
            "-f",
            str(COMPOSE),
            "config",
            "--format",
            "json",
        ],
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    )
    rendered = json.loads(result.stdout)
    (portal,) = rendered["x-portals"]
    (published,) = rendered["services"]["status"]["ports"]
    assert type(portal["port"]) is int and portal["port"] == 18790
    assert str(published["published"]) == str(portal["port"])
    assert published["target"] == 8080
    assert published["host_ip"] == portal["host"] == "192.0.2.10"
    assert portal["scheme"] == "http" and portal["path"] == "/"
