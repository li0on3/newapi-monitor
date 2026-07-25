from __future__ import annotations

import json
from typing import Any


def _error_message(payload: Any) -> str:
    if isinstance(payload, str):
        return payload.strip()
    if not isinstance(payload, dict):
        return ""
    response = payload.get("response")
    if isinstance(response, dict):
        nested = _error_message(response)
        if nested:
            return nested
    error = payload.get("error")
    if isinstance(error, str):
        return error.strip()
    if isinstance(error, dict):
        for key in ("message", "detail", "type", "code"):
            value = str(error.get(key) or "").strip()
            if value:
                return value
    for key in ("message", "detail", "msg"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    return ""


def validate_probe_json(request_format: str, payload: Any) -> tuple[bool, str]:
    if not isinstance(payload, dict):
        return False, "upstream returned a non-object JSON response"
    if payload.get("success") is False or payload.get("error"):
        return False, _error_message(payload) or "upstream returned an error response"

    response_type = str(payload.get("type") or "").strip().lower()
    status = str(payload.get("status") or "").strip().lower()
    if response_type == "error" or status in {"failed", "error", "cancelled", "canceled"}:
        return False, _error_message(payload) or f"upstream response status is {status or response_type}"

    if request_format == "responses":
        if status == "incomplete":
            return True, "response ended at the configured output limit"
        if status == "completed" or response_type == "response.completed":
            return True, ""
        output = payload.get("output")
        if isinstance(output, list) and output:
            return True, ""
        return False, "Responses payload has no completed status or output"

    if request_format == "chat":
        choices = payload.get("choices")
        if isinstance(choices, list) and choices:
            return True, ""
        return False, "Chat Completions payload has no choices"

    if request_format == "anthropic":
        if response_type == "message" and isinstance(payload.get("content"), list):
            return True, ""
        if payload.get("stop_reason") is not None and isinstance(payload.get("content"), list):
            return True, ""
        return False, "Anthropic payload has no message content"

    return False, f"unsupported probe format: {request_format}"


class ProbeProtocolValidator:
    def __init__(self, request_format: str):
        self.request_format = request_format
        self.valid_payload_seen = False
        self.completed = False
        self.error = ""

    def feed(self, event_name: str, data: str) -> None:
        if self.error:
            return
        event = event_name.strip().lower()
        text = data.strip()
        if not text or text == "[DONE]":
            if text == "[DONE]" and self.valid_payload_seen:
                self.completed = True
            return
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            self.error = "upstream returned invalid SSE JSON"
            return
        if not isinstance(payload, dict):
            self.error = "upstream returned a non-object SSE payload"
            return

        payload_type = str(payload.get("type") or "").strip().lower()
        if event == "error" or payload_type in {"error", "response.failed"} or event == "response.failed":
            self.error = _error_message(payload) or f"upstream emitted {event or payload_type}"
            return
        if payload.get("error"):
            self.error = _error_message(payload) or "upstream emitted an error payload"
            return

        if self.request_format == "responses":
            valid = (
                event.startswith("response.")
                or payload_type.startswith("response.")
                or isinstance(payload.get("output"), list)
            )
            if valid:
                self.valid_payload_seen = True
            if event in {"response.completed", "response.incomplete"} or payload_type in {
                "response.completed",
                "response.incomplete",
            }:
                self.completed = True
            return

        if self.request_format == "chat":
            choices = payload.get("choices")
            if isinstance(choices, list) and choices:
                self.valid_payload_seen = True
            return

        if self.request_format == "anthropic":
            valid_types = {
                "message_start",
                "content_block_start",
                "content_block_delta",
                "content_block_stop",
                "message_delta",
                "message_stop",
            }
            if event in valid_types or payload_type in valid_types:
                self.valid_payload_seen = True
            if event == "message_stop" or payload_type == "message_stop":
                self.completed = True
            return

        self.error = f"unsupported probe format: {self.request_format}"

    def result(self) -> tuple[bool, str]:
        if self.error:
            return False, self.error[:500]
        if not self.valid_payload_seen:
            return False, "upstream returned no valid protocol payload"
        if not self.completed:
            return False, "upstream stream ended before a terminal protocol event"
        return True, ""
