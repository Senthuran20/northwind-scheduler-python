"""Optional adapter for any OpenAI-compatible chat completions endpoint.

Works with OpenAI, Azure OpenAI (v1-compatible endpoints), Ollama
(http://localhost:11434/v1) and similar. Configure with environment variables:

    LLM_BASE_URL   e.g. http://localhost:11434/v1
    LLM_API_KEY    optional for local servers
    LLM_MODEL      e.g. llama3.1

You do NOT need this to complete the assignment. All grading uses FakeModel.
"""

import json
import os
import urllib.error
import urllib.request

from .model import ModelRequestError

REQUEST_TIMEOUT_SECONDS = 10


class OpenAICompatibleModel:
    def __init__(self):
        self.base_url = os.environ["LLM_BASE_URL"].rstrip("/")
        self.api_key = os.environ.get("LLM_API_KEY", "")
        self.model = os.environ["LLM_MODEL"]

    def complete(self, messages: list[dict]) -> str:
        # Most endpoints don't accept a bare "tool" role without tool_call ids,
        # so tool results are sent back as user messages.
        converted = [
            {"role": "user", "content": f"Tool result: {m['content']}"}
            if m["role"] == "tool"
            else m
            for m in messages
        ]
        body = json.dumps({"model": self.model, "messages": converted}).encode()
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=body,
            headers=headers,
        )
        try:
            with urllib.request.urlopen(
                request, timeout=REQUEST_TIMEOUT_SECONDS
            ) as response:
                payload = json.load(response)
            content = payload["choices"][0]["message"]["content"]
        except (
            urllib.error.URLError,
            TimeoutError,
            json.JSONDecodeError,
            KeyError,
            TypeError,
            IndexError,
        ) as exc:
            raise ModelRequestError(
                "the configured model endpoint failed or returned an invalid response"
            ) from exc
        if not isinstance(content, str):
            raise ModelRequestError(
                "the configured model endpoint returned a non-text response"
            )
        return content
