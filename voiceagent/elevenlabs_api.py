"""A small ElevenLabs REST client: only the calls this project needs, with retries on 429 and 5xx.

Auth is the `xi-api-key` header. Use a RESTRICTED key (only the scopes below) with a credit limit:
Text to Speech, ElevenAgents (write), Voices (read), Pronunciation Dictionaries (write), Models,
User, Webhooks. The key is read from the environment or the macOS Keychain, never from a file
in the repo.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

import httpx

from .config import ELEVENLABS_API_BASE

RETRY_STATUSES = {408, 429, 500, 502, 503, 504}


class ElevenLabsError(Exception):
    def __init__(self, status: int, message: str, body: Any = None):
        super().__init__(f"ElevenLabs API {status}: {message}")
        self.status = status
        self.body = body


def _message(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return resp.text[:300]
    detail = body.get("detail") if isinstance(body, dict) else body
    if isinstance(detail, dict):
        return str(detail.get("message") or detail.get("status") or detail)[:300]
    if isinstance(detail, list):  # 422 validation errors: [{"loc": [...], "msg": ...}]
        return "; ".join(f"{'.'.join(map(str, d.get('loc', [])))}: {d.get('msg')}" for d in detail if isinstance(d, dict))[:600]
    return str(detail)[:300]


class ElevenLabsClient:
    def __init__(self, api_key: str, base_url: str = ELEVENLABS_API_BASE, transport: Optional[httpx.BaseTransport] = None,
                 max_retries: int = 3, sleep=time.sleep, timeout: float = 30.0):
        if not api_key:
            raise ValueError("an ElevenLabs API key is required")
        self._http = httpx.Client(base_url=base_url, headers={"xi-api-key": api_key}, transport=transport,
                                  timeout=timeout)
        self.max_retries = max_retries
        self._sleep = sleep

    def close(self) -> None:
        self._http.close()

    def _request(self, method: str, path: str, *, raw: bool = False, **kwargs) -> Any:
        attempt = 0
        while True:
            resp = self._http.request(method, path, **kwargs)
            if resp.status_code in RETRY_STATUSES and attempt < self.max_retries:
                wait = resp.headers.get("retry-after")
                try:
                    delay = min(float(wait), 30.0) if wait else 2 ** attempt
                except ValueError:
                    delay = 2 ** attempt
                attempt += 1
                self._sleep(delay)
                continue
            if resp.status_code >= 400:
                body = None
                try:
                    body = resp.json()
                except ValueError:
                    pass
                raise ElevenLabsError(resp.status_code, _message(resp), body)
            if raw:
                return resp
            if resp.status_code == 204 or not resp.content:
                return {}
            return resp.json()

    # account -----------------------------------------------------------------------------------
    def subscription(self) -> Dict[str, Any]:
        return self._request("GET", "/v1/user/subscription")

    # secrets, tools, knowledge base ------------------------------------------------------------
    def create_secret(self, name: str, value: str) -> str:
        return self._request("POST", "/v1/convai/secrets", json={"type": "new", "name": name, "value": value})["secret_id"]

    def delete_secret(self, secret_id: str) -> None:
        self._request("DELETE", f"/v1/convai/secrets/{secret_id}")

    def create_tool(self, tool_config: Dict[str, Any]) -> str:
        return self._request("POST", "/v1/convai/tools", json={"tool_config": tool_config})["id"]

    def update_tool(self, tool_id: str, tool_config: Dict[str, Any]) -> None:
        self._request("PATCH", f"/v1/convai/tools/{tool_id}", json={"tool_config": tool_config})

    def delete_tool(self, tool_id: str) -> None:
        self._request("DELETE", f"/v1/convai/tools/{tool_id}")

    def create_kb_text(self, name: str, text: str) -> str:
        return self._request("POST", "/v1/convai/knowledge-base/text", json={"name": name, "text": text})["id"]

    def delete_kb_document(self, doc_id: str) -> None:
        self._request("DELETE", f"/v1/convai/knowledge-base/{doc_id}", params={"force": "true"})

    # pronunciation dictionaries ----------------------------------------------------------------
    def create_dictionary(self, name: str, rules: List[Dict[str, Any]], description: str = "") -> Dict[str, str]:
        out = self._request("POST", "/v1/pronunciation-dictionaries/add-from-rules",
                            json={"name": name, "rules": rules, "description": description or None})
        return {"pronunciation_dictionary_id": out["id"], "version_id": out["version_id"]}

    # webhooks ----------------------------------------------------------------------------------
    def create_webhook(self, name: str, url: str) -> Dict[str, Optional[str]]:
        out = self._request("POST", "/v1/workspace/webhooks",
                            json={"settings": {"auth_type": "hmac", "name": name, "webhook_url": url}})
        return {"webhook_id": out["webhook_id"], "webhook_secret": out.get("webhook_secret")}

    def delete_webhook(self, webhook_id: str) -> None:
        self._request("DELETE", f"/v1/workspace/webhooks/{webhook_id}")

    # agents and conversations ------------------------------------------------------------------
    def create_agent(self, body: Dict[str, Any]) -> str:
        return self._request("POST", "/v1/convai/agents/create", json=body)["agent_id"]

    def update_agent(self, agent_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        return self._request("PATCH", f"/v1/convai/agents/{agent_id}", json=body)

    def get_agent(self, agent_id: str) -> Dict[str, Any]:
        return self._request("GET", f"/v1/convai/agents/{agent_id}")

    def delete_agent(self, agent_id: str) -> None:
        self._request("DELETE", f"/v1/convai/agents/{agent_id}")

    def signed_url(self, agent_id: str) -> str:
        return self._request("GET", "/v1/convai/conversation/get-signed-url", params={"agent_id": agent_id})["signed_url"]

    def get_conversation(self, conversation_id: str) -> Dict[str, Any]:
        return self._request("GET", f"/v1/convai/conversations/{conversation_id}")

    # text to speech ----------------------------------------------------------------------------
    def text_to_speech(self, voice_id: str, text: str, model_id: str,
                       dictionaries: Optional[List[Dict[str, str]]] = None,
                       output_format: str = "mp3_44100_128", zero_retention: bool = False) -> httpx.Response:
        body: Dict[str, Any] = {"text": text, "model_id": model_id}
        if dictionaries:
            body["pronunciation_dictionary_locators"] = dictionaries
        params: Dict[str, str] = {"output_format": output_format}
        if zero_retention:
            params["enable_logging"] = "false"
        return self._request("POST", f"/v1/text-to-speech/{voice_id}", json=body, params=params, raw=True)
