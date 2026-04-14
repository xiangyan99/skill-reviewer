from __future__ import annotations

import asyncio
import json
import re
from typing import TYPE_CHECKING

from copilot import CopilotClient
from copilot.generated.session_events import SessionEventType
from copilot.session import PermissionHandler

if TYPE_CHECKING:
    from pydantic import BaseModel


async def copilot_chat(
    client: CopilotClient,
    model: str,
    messages: list[dict[str, str]],
    *,
    timeout: float = 600.0,
) -> str:
    """Send messages via a Copilot session and return the assistant's response."""
    system_content = None
    user_content = ""
    for msg in messages:
        if msg["role"] == "system":
            system_content = msg["content"]
        elif msg["role"] == "user":
            user_content = msg["content"]

    session_kwargs: dict = {
        "on_permission_request": PermissionHandler.approve_all,
        "model": model,
        "infinite_sessions": {"enabled": False},
        "available_tools": [],
    }
    if system_content:
        session_kwargs["system_message"] = {
            "mode": "replace",
            "content": system_content,
        }

    async with await client.create_session(**session_kwargs) as session:
        response = await session.send_and_wait(user_content, timeout=timeout)
        if response is None or response.type != SessionEventType.ASSISTANT_MESSAGE:
            return ""
        return response.data.content or ""


async def copilot_parse_structured(
    client: CopilotClient,
    model: str,
    messages: list[dict[str, str]],
    schema: type[BaseModel],
    *,
    timeout: float = 600.0,
) -> BaseModel:
    """Send messages and parse the response as a Pydantic model.

    The JSON schema is appended to the system message so the model returns
    valid JSON matching the expected structure.
    """
    schema_json = json.dumps(schema.model_json_schema(), indent=2)
    json_instruction = (
        "\n\nYou MUST respond with valid JSON matching this exact schema:\n"
        f"{schema_json}\n"
        "Output raw JSON only. No markdown code fences, no extra text."
    )

    enhanced_messages = []
    for msg in messages:
        if msg["role"] == "system":
            enhanced_messages.append({
                "role": "system",
                "content": msg["content"] + json_instruction,
            })
        else:
            enhanced_messages.append(msg)

    response_text = await copilot_chat(client, model, enhanced_messages, timeout=timeout)
    json_str = _extract_json(response_text)
    return schema.model_validate_json(json_str)


def _extract_json(text: str) -> str:
    """Extract JSON from a response that may contain markdown code fences."""
    text = text.strip()
    # Try to find JSON in code fences
    match = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    # If already looks like JSON, use directly
    if text.startswith(("{", "[")):
        return text
    # Find the outermost JSON object or array
    for start_char, end_char in [("{", "}"), ("[", "]")]:
        start = text.find(start_char)
        end = text.rfind(end_char)
        if start != -1 and end != -1 and end > start:
            return text[start : end + 1]
    return text
