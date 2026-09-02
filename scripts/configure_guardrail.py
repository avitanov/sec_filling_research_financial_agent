"""Create or update the project's Amazon Bedrock guardrail."""

import argparse
import sys
from pathlib import Path
from time import sleep
from typing import Any

import boto3

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from config import AWS_REGION  # noqa: E402
from guardrails import GUARDRAIL_NAME, GUARDRAIL_TAGS, guardrail_policy  # noqa: E402


def _find_guardrail(client: Any) -> str | None:
    response = client.list_guardrails(maxResults=100)
    matches = [
        guardrail["id"]
        for guardrail in response.get("guardrails", [])
        if guardrail.get("name") == GUARDRAIL_NAME
    ]
    if len(matches) > 1:
        raise RuntimeError(f"Multiple guardrails are named {GUARDRAIL_NAME!r}")
    return matches[0] if matches else None


def _wait_until_ready(client: Any, guardrail_id: str, version: str) -> None:
    for _ in range(30):
        response = client.get_guardrail(
            guardrailIdentifier=guardrail_id,
            guardrailVersion=version,
        )
        status = response["status"]
        if status == "READY":
            return
        if status == "FAILED":
            raise RuntimeError(f"Guardrail {guardrail_id}:{version} failed")
        sleep(2)
    raise TimeoutError(f"Guardrail {guardrail_id}:{version} was not ready in time")


def _parse_args() -> argparse.Namespace:
    """Parse the provisioning options."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--create-only",
        action="store_true",
        help="Create a new guardrail without calling ListGuardrails.",
    )
    return parser.parse_args()


def main() -> None:
    """Upsert the draft, create an immutable version, and print its settings."""
    args = _parse_args()
    client = boto3.client("bedrock", region_name=AWS_REGION)
    policy = guardrail_policy()
    guardrail_id = None if args.create_only else _find_guardrail(client)
    if guardrail_id is None:
        guardrail_id = client.create_guardrail(**policy, tags=GUARDRAIL_TAGS)[
            "guardrailId"
        ]
    else:
        client.update_guardrail(guardrailIdentifier=guardrail_id, **policy)
        guardrail = client.get_guardrail(
            guardrailIdentifier=guardrail_id,
            guardrailVersion="DRAFT",
        )
        client.tag_resource(resourceArn=guardrail["guardrailArn"], tags=GUARDRAIL_TAGS)

    _wait_until_ready(client, guardrail_id, "DRAFT")
    version = client.create_guardrail_version(
        guardrailIdentifier=guardrail_id,
        description="Read-only financial research policy",
    )["version"]
    _wait_until_ready(client, guardrail_id, version)

    print(f'BEDROCK_GUARDRAIL_ID="{guardrail_id}"')
    print(f'BEDROCK_GUARDRAIL_VERSION="{version}"')


if __name__ == "__main__":
    main()
