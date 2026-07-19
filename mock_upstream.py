import json
import os
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


STATE = {"mode": "healthy", "delay": 0.2}


class Handler(BaseHTTPRequestHandler):
    def _json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/control":
            query = urllib.parse.parse_qs(parsed.query)
            mode = query.get("mode", [STATE["mode"]])[0]
            if mode not in {"healthy", "slow", "fail"}:
                self._json(400, {"error": "invalid mode"})
                return
            STATE["mode"] = mode
            if "delay" in query:
                STATE["delay"] = float(query["delay"][0])
            self._json(200, STATE)
            return
        if parsed.path == "/health":
            self._json(200, {"status": "ok", **STATE})
            return
        self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path not in {"/v1/chat/completions", "/v1/responses"}:
            self._json(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        mode = STATE["mode"]
        if mode == "fail":
            self._json(500, {"error": {"message": "mock upstream failure", "type": "mock_error"}})
            return
        delay = 3.0 if mode == "slow" else float(STATE["delay"])
        time.sleep(delay)
        model = payload.get("model", "gpt-test")
        if payload.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            if self.path == "/v1/responses":
                events = [
                    {"type": "response.created", "response": {"id": "resp_mock", "status": "in_progress", "model": model}},
                    {"type": "response.output_text.delta", "item_id": "msg_mock", "output_index": 0, "content_index": 0, "delta": "OK"},
                    {"type": "response.completed", "response": {"id": "resp_mock", "status": "completed", "model": model, "output": []}},
                ]
                for event in events:
                    self.wfile.write(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode("utf-8"))
                    self.wfile.flush()
                return
            chunks = [
                {
                    "id": "chatcmpl-mock",
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": model,
                    "choices": [{"index": 0, "delta": {"role": "assistant", "content": "OK"}, "finish_reason": None}],
                },
                {
                    "id": "chatcmpl-mock",
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": model,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                },
            ]
            for chunk in chunks:
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode("utf-8"))
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            return
        if self.path == "/v1/responses":
            self._json(
                200,
                {
                    "id": "resp_mock",
                    "object": "response",
                    "created_at": int(time.time()),
                    "status": "completed",
                    "model": model,
                    "output": [],
                    "usage": {"input_tokens": 4, "output_tokens": 2, "total_tokens": 6},
                },
            )
            return
        self._json(
            200,
            {
                "id": "chatcmpl-mock",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "mock response"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6},
            },
        )

    def log_message(self, format, *args):
        print(f"mock-upstream {self.address_string()} {format % args}", flush=True)


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
