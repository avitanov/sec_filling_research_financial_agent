"""Check configured Bedrock models with one small inference request."""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import requests
from boto3 import Session
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.config import Config
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    CredentialRetrievalError,
    NoCredentialsError,
    SSOTokenLoadError,
    UnauthorizedSSOTokenError,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from config import (  # noqa: E402
    AWS_REGION,
    BEDROCK_HAIKU_MODEL_ID,
    BEDROCK_LUNA_MODEL_ID,
    BEDROCK_MANTLE_TIMEOUT_SECONDS,
    BEDROCK_NOVA_LITE_MODEL_ID,
    BEDROCK_SONNET_MODEL_ID,
)


def _signed_post(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str] | None = None,
) -> requests.Response:
    credentials = Session().get_credentials()
    if credentials is None:
        raise RuntimeError(
            "AWS credentials were not found. Check AWS_PROFILE and its "
            "credential_process configuration."
        )

    request_body = json.dumps(payload)
    request_headers = {"Content-Type": "application/json", **(headers or {})}
    aws_request = AWSRequest(
        method="POST",
        url=url,
        data=request_body,
        headers=request_headers,
    )
    SigV4Auth(
        credentials.get_frozen_credentials(),
        "bedrock-mantle",
        AWS_REGION,
    ).add_auth(aws_request)

    return requests.post(
        url,
        data=request_body,
        headers=dict(aws_request.headers.items()),
        timeout=BEDROCK_MANTLE_TIMEOUT_SECONDS,
    )


def _check_response(response: requests.Response, model_id: str) -> None:
    if response.ok:
        return

    detail = response.text.strip()[:500]
    if response.status_code in {401, 403}:
        raise RuntimeError(
            f"{model_id} request was forbidden (HTTP {response.status_code}). "
            "Confirm that your SSO role has AmazonBedrockMantleInferenceAccess "
            "and access to this model in the selected region."
            + (f" AWS response: {detail}" if detail else "")
        )
    if response.status_code == 429:
        raise RuntimeError(
            f"{model_id} was throttled by Bedrock Mantle (HTTP 429). Try again."
        )
    if response.status_code >= 500:
        raise RuntimeError(
            f"Bedrock Mantle is unavailable for {model_id} "
            f"(HTTP {response.status_code}). Try again later."
        )
    raise RuntimeError(
        f"{model_id} request failed with HTTP {response.status_code}."
        + (f" AWS response: {detail}" if detail else "")
    )


def _call_converse(
    model_id: str,
    model_name: str,
) -> tuple[str, dict[str, Any]]:
    client = Session().client(
        "bedrock-runtime",
        region_name=AWS_REGION,
        config=Config(
            connect_timeout=BEDROCK_MANTLE_TIMEOUT_SECONDS,
            read_timeout=BEDROCK_MANTLE_TIMEOUT_SECONDS,
        ),
    )
    try:
        body: dict[str, Any] = client.converse(
            modelId=model_id,
            messages=[
                {
                    "role": "user",
                    "content": [{"text": "What is 2 + 2? Reply with only the number."}],
                }
            ],
            inferenceConfig={"maxTokens": 16, "temperature": 0},
        )
    except ClientError as error:
        error_code = error.response.get("Error", {}).get("Code", "Unknown")
        raise RuntimeError(
            f"{model_name} failed through Bedrock Runtime ({error_code}). Confirm "
            "bedrock:InvokeModel permission and access to the configured "
            "inference profile."
        ) from error

    text_blocks = [
        block["text"]
        for block in body.get("output", {}).get("message", {}).get("content", [])
        if block.get("text")
    ]
    if not text_blocks:
        raise RuntimeError(f"{model_name} returned no text: {body}")

    return " ".join(text_blocks), body.get("usage", {})


def _call_luna() -> tuple[str, dict[str, Any]]:
    url = f"https://bedrock-mantle.{AWS_REGION}.api.aws/openai/v1/responses"
    response = _signed_post(
        url,
        {
            "model": BEDROCK_LUNA_MODEL_ID,
            "input": "What is 2 + 2? Reply with only the number.",
            "reasoning": {"effort": "none"},
            "max_output_tokens": 16,
            "store": False,
        },
    )
    _check_response(response, BEDROCK_LUNA_MODEL_ID)
    body: dict[str, Any] = response.json()
    text_blocks = [
        content["text"]
        for item in body.get("output", [])
        if item.get("type") == "message"
        for content in item.get("content", [])
        if content.get("type") == "output_text" and content.get("text")
    ]
    if not text_blocks:
        raise RuntimeError(f"Luna returned no text: {body}")

    return " ".join(text_blocks), body.get("usage", {})


def main() -> None:
    """Send one small request to the selected model."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--model", choices=("nova-lite", "sonnet", "haiku", "luna"), required=True
    )
    args = parser.parse_args()

    try:
        if args.model == "nova-lite":
            model_id = BEDROCK_NOVA_LITE_MODEL_ID
            response_text, usage = _call_converse(model_id, "Nova Lite")
        elif args.model == "sonnet":
            model_id = BEDROCK_SONNET_MODEL_ID
            response_text, usage = _call_converse(model_id, "Sonnet")
        elif args.model == "haiku":
            model_id = BEDROCK_HAIKU_MODEL_ID
            response_text, usage = _call_converse(model_id, "Haiku")
        else:
            model_id = BEDROCK_LUNA_MODEL_ID
            response_text, usage = _call_luna()
    except (
        CredentialRetrievalError,
        NoCredentialsError,
        SSOTokenLoadError,
        UnauthorizedSSOTokenError,
    ) as error:
        profile = os.getenv("AWS_PROFILE", "default")
        raise SystemExit(
            "UNAVAILABLE: AWS credentials could not be loaded. Run "
            f"`aws sso login --profile {profile}` and try again. "
            f"Reason: {error}"
        ) from None
    except BotoCoreError as error:
        raise SystemExit(f"UNAVAILABLE: AWS request failed. Reason: {error}") from None
    except requests.Timeout:
        raise SystemExit(
            f"UNAVAILABLE: Bedrock Mantle timed out after "
            f"{BEDROCK_MANTLE_TIMEOUT_SECONDS} seconds."
        ) from None
    except requests.RequestException as error:
        raise SystemExit(
            f"UNAVAILABLE: Could not reach Bedrock Mantle. Reason: {error}"
        ) from None
    except RuntimeError as error:
        raise SystemExit(f"UNAVAILABLE: {error}") from None

    print(f"AVAILABLE: {model_id}")
    print(f"Response: {response_text}")
    print(f"Usage: {json.dumps(usage, sort_keys=True)}")


if __name__ == "__main__":
    main()
