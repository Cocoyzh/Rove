import io
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import anthropic
import httpx
import pytest

from rove.headless import EXIT_CODES, Terminated, classify_exception, deny_approval, execute
from rove.lead_agent import LeadAgent
from rove.llm import LLMResponse
from rove.messages import ToolCall
from rove.observers import JsonlObserver
from rove.permissions import PermissionPolicy
from rove.tool_registry import Tool, ToolRegistry
from test_observers import ScriptedLLM

SRC = Path(__file__).resolve().parents[1] / "src"


# ---------- 进程内：execute 的收尾逻辑 ----------

@pytest.fixture
def isolated_bus(tmp_path, monkeypatch):
    from rove.tools.background import BackGroundManager
    from rove.tools.message_bus import MessageBus
    monkeypatch.setattr("rove.lead_agent.BUS", MessageBus(tmp_path / "inbox"))
    monkeypatch.setattr("rove.lead_agent.bg_manager", BackGroundManager())


def _events(buf: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in buf.getvalue().splitlines()]


def _agent_factory(tmp_path, responses, observer):
    def build():
        registry = ToolRegistry(PermissionPolicy(tmp_path), deny_approval)
        registry.register(Tool("bash", "run shell", {"type": "object", "properties": {}},
                               handler=lambda **kw: "SHOULD NOT RUN"))
        return LeadAgent(ScriptedLLM(responses), registry, max_steps=5, observer=observer)
    return build


@pytest.mark.parametrize("stop_reason, reason, code", [
    ("end_turn", "completed", 0),
    ("max_tokens", "max_tokens", 5),
])
def test_execute_brackets_run_with_started_and_finished(tmp_path, isolated_bus, stop_reason, reason, code):
    buf = io.StringIO()
    obs = JsonlObserver(buf)
    final = LLMResponse(content="answer", tool_calls=[], model="m", stop_reason=stop_reason)
    assert execute(_agent_factory(tmp_path, [final], obs), "go", obs, model="m") == code

    events = _events(buf)
    assert events[0]["type"] == "run_started" and events[0]["model"] == "m"
    last = events[-1]
    assert last["type"] == "run_finished"
    assert (last["reason"], last["exit_code"], last["final_text"]) == (reason, code, "answer")


@pytest.mark.parametrize("exc, reason", [
    (Terminated(), "cancelled"),
    (anthropic.AuthenticationError("bad key", response=httpx.Response(401, request=httpx.Request("POST", "http://x")), body=None), "auth_failed"),
    (RuntimeError("bug"), "crashed"),
])
def test_execute_still_emits_run_finished_when_run_raises(exc, reason):
    buf = io.StringIO()
    obs = JsonlObserver(buf)

    def build():
        raise exc
    assert execute(build, "go", obs) == EXIT_CODES[reason]
    last = _events(buf)[-1]
    assert last["type"] == "run_finished" and last["reason"] == reason


def test_headless_denies_ask_without_reading_stdin(tmp_path, isolated_bus, monkeypatch):
    """bash 在 workspace 模式下需要审批；headless 必须拒绝，且不能碰 input()。"""
    monkeypatch.setattr("builtins.input", lambda *a: pytest.fail("headless 调用了 input()"))
    buf = io.StringIO()
    obs = JsonlObserver(buf)
    responses = [
        LLMResponse(content="", tool_calls=[ToolCall("c_1", "bash", {"command": "rm -r build"})],
                    model="m", stop_reason="tool_use"),
        LLMResponse(content="换个办法", tool_calls=[], model="m", stop_reason="end_turn"),
    ]
    assert execute(_agent_factory(tmp_path, responses, obs), "go", obs) == 0
    result = next(e for e in _events(buf) if e["type"] == "tool_result")
    assert result["preview"].startswith("Permission Denied")
    assert "SHOULD NOT RUN" not in buf.getvalue()


def test_permission_denied_is_reported_as_error(tmp_path, isolated_bus):
    buf = io.StringIO()
    obs = JsonlObserver(buf)
    responses = [
        LLMResponse(content="", tool_calls=[ToolCall("c_1", "bash", {"command": "rm -r build"})],
                    model="m", stop_reason="tool_use"),
        LLMResponse(content="ok", tool_calls=[], model="m", stop_reason="end_turn"),
    ]
    execute(_agent_factory(tmp_path, responses, obs), "go", obs)
    result = next(e for e in _events(buf) if e["type"] == "tool_result")
    assert result["is_error"] is True
    assert result["error_kind"] == "approval_denied"


@pytest.mark.parametrize("exc, reason", [
    (anthropic.APITimeoutError(request=httpx.Request("POST", "http://x")), "llm_timeout"),
    (anthropic.APIConnectionError(request=httpx.Request("POST", "http://x")), "llm_unavailable"),
])
def test_timeout_is_classified_before_its_parent_class(exc, reason):
    # APITimeoutError 是 APIConnectionError 的子类：判断顺序反了就会全部变成 llm_unavailable
    assert classify_exception(exc)[0] == reason


@pytest.mark.parametrize("status, reason", [
    (429, "rate_limited"), (401, "auth_failed"), (403, "auth_failed"),
    (500, "llm_unavailable"), (529, "llm_unavailable"), (404, "bad_request"),
])
def test_status_codes_map_to_reason(status, reason):
    resp = httpx.Response(status, request=httpx.Request("POST", "http://x"))
    exc = anthropic.APIStatusError("x", response=resp, body=None)
    assert classify_exception(exc)[0] == reason


# ---------- 子进程：真实的退出码、stdout 纯净度、SIGTERM ----------

def _rove_run(*args, env_overrides=None, **popen_kw):
    env = {**os.environ, "PYTHONPATH": str(SRC), "LLM_MODEL_ID": "m", "LLM_API_KEY": "k",
           **(env_overrides or {})}
    return subprocess.Popen([sys.executable, "-m", "rove.main", "run", *args],
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, env=env, **popen_kw)


def _assert_pure_jsonl(stdout: str) -> list[dict]:
    lines = stdout.splitlines()
    events = [json.loads(line) for line in lines]   # 任何非 JSON 行都会在这里炸
    assert events[0]["type"] == "run_started"
    assert events[-1]["type"] == "run_finished"
    return events


def test_cli_bad_args_exit_2_and_no_events():
    p = _rove_run("--no-such-flag")
    out, err = p.communicate(timeout=30)
    assert p.returncode == 2
    assert out == ""
    assert "unrecognized arguments" in err


def test_cli_unreachable_llm_exit_3(tmp_path):
    with socket.socket() as s:     # 拿一个当前没人监听的端口
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    p = _rove_run("--prompt", "hi", cwd=tmp_path,
                  env_overrides={"LLM_BASE_URL": f"http://127.0.0.1:{port}", "LLM_TIMEOUT": "2"})
    out, err = p.communicate(timeout=60)
    events = _assert_pure_jsonl(out)
    assert p.returncode == 3
    assert events[-1]["reason"] == "llm_unavailable"


def test_cli_sigterm_emits_cancelled_and_exits_143(tmp_path):
    # 一个只 listen 不回包的"LLM"：请求会一直挂着，模拟长时间运行中的任务
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen()
    port = server.getsockname()[1]
    try:
        p = _rove_run("--prompt", "hi", cwd=tmp_path,
                      env_overrides={"LLM_BASE_URL": f"http://127.0.0.1:{port}", "LLM_TIMEOUT": "60"})
        assert json.loads(p.stdout.readline())["type"] == "run_started"
        conn, _ = server.accept()        # 确认请求已经发出，Rove 正卡在等响应
        p.send_signal(signal.SIGTERM)
        out, err = p.communicate(timeout=30)
        conn.close()
    finally:
        server.close()

    assert p.returncode == 143
    events = [json.loads(line) for line in out.splitlines()]
    assert events[-1]["type"] == "run_finished"
    assert events[-1]["reason"] == "cancelled"


# ---------- Codex review P2：teammate 不继承非交互审批 → headless 拒绝启动 ----------

def test_headless_refuses_teammates(monkeypatch):
    import argparse
    from rove.headless import _build_agent
    monkeypatch.setenv("LLM_MODEL_ID", "m")
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("ROVE_TEAMMATES", "1")
    buf = io.StringIO()
    obs = JsonlObserver(buf)
    args = argparse.Namespace(approval_mode="strict", max_steps=5)
    assert execute(lambda: _build_agent(args, obs), "go", obs) == EXIT_CODES["config_error"]
    assert "ROVE_TEAMMATES" in _events(buf)[-1]["detail"]


# ---------- Codex review P1：先清理后台子进程，再发 run_finished ----------

def test_background_processes_are_stopped_before_run_finished(monkeypatch):
    from rove.tools import background
    bg = background.BackGroundManager()
    monkeypatch.setattr(background, "bg_manager", bg)
    buf = io.StringIO()
    obs = JsonlObserver(buf)
    procs = []

    def build():
        bg.run("exec sleep 30")
        for _ in range(50):                       # 等后台线程把进程登记上
            if bg._procs:
                break
            time.sleep(0.05)
        procs.extend(bg._procs.values())
        raise Terminated()

    assert execute(build, "go", obs) == 143
    last = _events(buf)[-1]
    assert (last["reason"], last["background_terminated"]) == ("cancelled", 1)
    assert procs and procs[0].poll() is not None   # 发 run_finished 时进程已经退出了
