"""Check the configured OpenRouter model with one small inference request."""

import json
import sys
from pathlib import Path
from typing import Any

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from config import (  # noqa: E402
    OPENROUTER_API_KEY,
    OPENROUTER_BASE_URL,
    OPENROUTER_MODEL,
    OPENROUTER_PROVIDER,
    OPENROUTER_REASONING_EFFORT,
    OPENROUTER_TIMEOUT_SECONDS,
)


def _check_env_file() -> None:
    if not (PROJECT_ROOT / ".env").exists():
        raise RuntimeError(
            "Missing .env file. Copy .env.example and add OPENROUTER_API_KEY."
        )
    if not OPENROUTER_API_KEY:
        raise RuntimeError("Missing OPENROUTER_API_KEY in .env.")


def _check_response(response: requests.Response) -> None:
    if response.ok:
        return

    detail = response.text.strip()[:500]
    if response.status_code in {401, 403}:
        raise RuntimeError(
            f"OpenRouter request was forbidden (HTTP {response.status_code})."
            + (f" Provider response: {detail}" if detail else "")
        )
    if response.status_code == 429:
        raise RuntimeError(
            "OpenRouter request was throttled (HTTP 429)."
            + (f" Provider response: {detail}" if detail else "")
        )
    if response.status_code >= 500:
        raise RuntimeError(
            "OpenRouter is unavailable."
            + (f" Provider response: {detail}" if detail else "")
        )
    raise RuntimeError(
        f"OpenRouter request failed with HTTP {response.status_code}."
        + (f" Provider response: {detail}" if detail else "")
    )


def _call_model() -> tuple[str, dict[str, Any]]:
    response = requests.post(
        f"{OPENROUTER_BASE_URL.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
        json={
            "model": OPENROUTER_MODEL,
            "messages": [{"role": "user", "content": "Reply with: OK."}],
            "temperature": 0,
            "max_tokens": 16,
            "reasoning": {"enabled": True, "effort": OPENROUTER_REASONING_EFFORT},
            "provider": {
                "only": [OPENROUTER_PROVIDER],
                "allow_fallbacks": False,
                "require_parameters": True,
            },
        },
        timeout=OPENROUTER_TIMEOUT_SECONDS,
    )
    _check_response(response)
    body: dict[str, Any] = response.json()
    choices = body.get("choices", [])
    if not choices:
        raise RuntimeError(f"OpenRouter returned no choices: {body}")
    message = choices[0].get("message", {})
    content = message.get("content", "")
    if not content:
        raise RuntimeError(f"OpenRouter returned no text: {body}")
    return str(content), dict(body.get("usage", {}))


def main() -> None:
    """Check the local configuration and send one minimal request."""
    try:
        _check_env_file()
        text, usage = _call_model()
    except requests.Timeout as error:
        raise SystemExit(
            f"UNAVAILABLE: OpenRouter timed out after "
            f"{OPENROUTER_TIMEOUT_SECONDS} seconds."
        ) from error
    except requests.RequestException as error:
        raise SystemExit(
            f"UNAVAILABLE: OpenRouter request failed. Reason: {error}"
        ) from error
    except RuntimeError as error:
        raise SystemExit(f"UNAVAILABLE: {error}") from error

    print(f"AVAILABLE: {OPENROUTER_MODEL} via {OPENROUTER_PROVIDER}")
    print(f"Response: {text}")
    print(f"Usage: {json.dumps(usage, sort_keys=True)}")


if __name__ == "__main__":
    main()
