"""Fournisseurs réels (SDK Anthropic et OpenAI) contre de faux serveurs HTTP locaux : streaming, outils, liste des modèles."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from etl_assistant.agent import run_turn
from etl_assistant.providers import (
    AnthropicProvider,
    OpenAIProvider,
    ProviderError,
    make_provider,
    to_openai_messages,
)
from etl_assistant.tools import TOOLS, Toolbox
from test_agent import FLOW, StubBackend


def sse(*events: tuple[str | None, dict]) -> bytes:
    out = b""
    for name, payload in events:
        out += (f"event: {name}\n" if name else "").encode() + f"data: {json.dumps(payload)}\n\n".encode()
    return out


class Server:
    """Un faux fournisseur : réponses mises en file, requêtes reçues conservées."""

    def __init__(self):
        self.requests: list[tuple[str, dict, dict]] = []
        self.responses: list[tuple[int, bytes, str]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def _answer(self):
                length = int(self.headers.get("content-length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}") if length else {}
                outer.requests.append((self.path, body, {k.lower(): v for k, v in self.headers.items()}))
                status, data, kind = outer.responses.pop(0)
                self.send_response(status)
                self.send_header("content-type", kind)
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = _answer

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def push(self, status: int, data: bytes | dict, kind: str = "application/json"):
        self.responses.append((status, data if isinstance(data, bytes) else json.dumps(data).encode(), kind))


@pytest.fixture
def server():
    s = Server()
    yield s
    s.httpd.shutdown()


def chunk(delta: dict, finish: str | None = None) -> tuple[None, dict]:
    return None, {
        "id": "c1",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "m",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }


def openai_tool_reply() -> bytes:
    return (
        sse(
            chunk({"role": "assistant", "content": "Je re"}),
            chunk({"content": "garde."}),
            chunk(
                {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "get_flow", "arguments": ""},
                        }
                    ]
                }
            ),
            chunk({"tool_calls": [{"index": 0, "function": {"arguments": "{}"}}]}),
            chunk({}, "tool_calls"),
        )
        + b"data: [DONE]\n\n"
    )


def test_openai_adapter_streams_text_and_assembles_tool_calls(server):
    server.push(200, openai_tool_reply(), "text/event-stream")
    provider = OpenAIProvider("", server.url + "/v1")  # sans clé, comme Ollama
    history = [{"role": "user", "content": "bonjour"}]
    events = list(provider.stream(system="sys", messages=history, tools=TOOLS, model="m", max_tokens=100))
    assert [e[1] for e in events if e[0] == "text"] == ["Je re", "garde."]
    turn = events[-1][1]
    assert turn.text == "Je regarde." and [(c.name, c.input) for c in turn.tool_calls] == [("get_flow", {})]
    assert turn.stop_reason == "tool_use"
    path, body, headers = server.requests[0]
    assert path == "/v1/chat/completions" and body["stream"] and body["model"] == "m"
    assert body["messages"][0] == {"role": "system", "content": "sys"}
    assert body["tools"][0]["type"] == "function" and len(body["tools"]) == len(TOOLS)


def test_openai_message_conversion_keeps_tool_results_right_after_their_call():
    messages = [
        {"role": "user", "content": "q"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "ok"},
                {"type": "tool_use", "id": "t1", "name": "get_flow", "input": {"a": 1}},
            ],
        },
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "{}"}]},
    ]
    out = to_openai_messages("sys", messages)
    assert [m["role"] for m in out] == ["system", "user", "assistant", "tool"]
    assert out[2]["tool_calls"][0]["function"] == {"name": "get_flow", "arguments": '{"a": 1}'}
    assert out[3] == {"role": "tool", "tool_call_id": "t1", "content": "{}"}


def test_openai_invalid_tool_arguments_are_reported_to_the_model(server):
    server.push(
        200,
        sse(
            chunk(
                {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "c",
                            "type": "function",
                            "function": {"name": "get_flow", "arguments": "{oups"},
                        }
                    ]
                }
            ),
            chunk({}, "tool_calls"),
        )
        + b"data: [DONE]\n\n",
        "text/event-stream",
    )
    provider = OpenAIProvider("k", server.url + "/v1")
    turn = list(
        provider.stream(system="s", messages=[{"role": "user", "content": "q"}], tools=TOOLS, model="m", max_tokens=10)
    )[-1][1]
    assert turn.tool_calls[0].error and not turn.tool_calls[0].input


def test_openai_models_and_errors(server):
    server.push(
        200,
        {
            "object": "list",
            "data": [
                {"id": "b", "object": "model", "created": 0, "owned_by": "x"},
                {"id": "a", "object": "model", "created": 0, "owned_by": "x"},
            ],
        },
    )
    assert OpenAIProvider("k", server.url + "/v1").models() == ["a", "b"]
    server.push(401, {"error": {"message": "bad key", "type": "invalid_request_error"}})
    with pytest.raises(ProviderError, match="clé refusée"):
        OpenAIProvider("bad", server.url + "/v1").models()


def anthropic_tool_reply() -> bytes:
    return sse(
        (
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": "m1",
                    "type": "message",
                    "role": "assistant",
                    "model": "claude-x",
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 11, "output_tokens": 1},
                },
            },
        ),
        (
            "content_block_start",
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        ),
        (
            "content_block_delta",
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Je regarde"}},
        ),
        (
            "content_block_delta",
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": " le flux."}},
        ),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        (
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 1,
                "content_block": {"type": "tool_use", "id": "toolu_1", "name": "get_flow", "input": {}},
            },
        ),
        (
            "content_block_delta",
            {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": "{}"}},
        ),
        ("content_block_stop", {"type": "content_block_stop", "index": 1}),
        (
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "tool_use", "stop_sequence": None},
                "usage": {"output_tokens": 20},
            },
        ),
        ("message_stop", {"type": "message_stop"}),
    )


def test_anthropic_streams_and_marks_the_prompt_for_caching(server):
    server.push(200, anthropic_tool_reply(), "text/event-stream")
    provider = AnthropicProvider("sk-test", server.url)
    events = list(
        provider.stream(
            system="sys", messages=[{"role": "user", "content": "q"}], tools=TOOLS, model="claude-x", max_tokens=100
        )
    )
    assert "".join(e[1] for e in events if e[0] == "text") == "Je regarde le flux."
    turn = events[-1][1]
    assert turn.tool_calls[0].name == "get_flow" and turn.stop_reason == "tool_use" and turn.usage["output"] == 20
    path, body, headers = server.requests[0]
    assert path == "/v1/messages" and body["stream"] and headers.get("x-api-key") == "sk-test"
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"} and body["tools"][-1]["cache_control"] == {
        "type": "ephemeral"
    }


def test_anthropic_models_and_errors(server):
    server.push(
        200,
        {
            "data": [{"id": "claude-x", "type": "model", "display_name": "X", "created_at": "2025-01-01T00:00:00Z"}],
            "has_more": False,
            "first_id": "claude-x",
            "last_id": "claude-x",
        },
    )
    assert AnthropicProvider("k", server.url).models() == ["claude-x"]
    server.push(401, {"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}})
    with pytest.raises(ProviderError, match="clé refusée"):
        AnthropicProvider("bad", server.url).models()


def test_a_real_provider_drives_the_agent_loop_end_to_end(server):
    server.push(200, anthropic_tool_reply(), "text/event-stream")
    final = sse(
        (
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": "m2",
                    "type": "message",
                    "role": "assistant",
                    "model": "claude-x",
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 30, "output_tokens": 1},
                },
            },
        ),
        (
            "content_block_start",
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        ),
        (
            "content_block_delta",
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Un Reader."}},
        ),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        (
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": 5},
            },
        ),
        ("message_stop", {"type": "message_stop"}),
    )
    server.push(200, final, "text/event-stream")
    box = Toolbox(StubBackend(), flow=FLOW)
    events = list(
        run_turn(
            AnthropicProvider("k", server.url), "claude-x", "sys", [{"role": "user", "content": "décris le flux"}], box
        )
    )
    assert "".join(e["delta"] for e in events if e["type"] == "text") == "Je regarde le flux.Un Reader."
    second = server.requests[1][1]["messages"]  # le résultat de l'outil est renvoyé au modèle, avec le bon identifiant
    assert second[-1]["content"][0]["type"] == "tool_result" and second[-1]["content"][0]["tool_use_id"] == "toolu_1"
    assert "Reader_1" in second[-1]["content"][0]["content"]
    assert events[-1]["type"] == "done" and events[-1]["usage"]["output"] == 25


def test_make_provider():
    assert make_provider("fake").models() == ["fake-1"]
    with pytest.raises(ProviderError):
        make_provider("inconnu")
