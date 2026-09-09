import pytest
from pathlib import Path
from unittest.mock import MagicMock
from rove.messages import Message, ToolCall
from rove.llm import LLMResponse, LLMRequest
from rove.llm_adapters import AnthropicLLMAdapter, BaseLLMAdapter
from rove.compaction.compaction_layers import (
    estimate_messages_tokens,
    tool_result_budget,
    micro_compact,
    snip_compact,
    compact_history,
    run_pipeline,
    KEEP_RECENT,
    COMPACT_THRESHOLD,
    PERSIST_THRESHOLD,
)


class DummyLLM(BaseLLMAdapter):
    def __init__(self, context_window=1_000_000):
        super().__init__(model="test-model", api_key="dummy", base_url=None, timeout=30, context_window=context_window)

    def _create_client(self):
        return None

    def complete(self, request: LLMRequest) -> LLMResponse:
        return LLMResponse(
            content="这是一个全局对话摘要，记录了用户的核心诉求与关键发现。",
            tool_calls=[],
            model=self.model,
            stop_reason="end_turn",
        )

    def stream(self, request: LLMRequest, on_text=None) -> LLMResponse:
        return self.complete(request)


def test_estimate_messages_tokens():
    """测试轻量 Token 估算器"""
    messages = [
        Message(role="user", content="Hello world!"),
        Message(role="assistant", content="你好，世界！这是一段中文测试。"),
    ]
    tokens = estimate_messages_tokens(messages)
    assert tokens > 10
    # 验证加入超长代码时估算值正确增加
    messages.append(Message(role="tool", tool_call_id="call_1", content="print('x')\n" * 1000))
    tokens_after = estimate_messages_tokens(messages)
    assert tokens_after > tokens + 2000


def test_delta_calibrated_tokens():
    """测试基于 API 实际返回的增量校准估算"""
    dummy_llm = DummyLLM()
    dummy_llm.last_input_tokens = 5000
    dummy_llm.last_output_tokens = 200

    messages = [
        Message(role="user", content="前置对话"),
        Message(role="assistant", content="回答内容"),
        Message(role="tool", tool_call_id="call_new", content="新增的工具输出内容"),
    ]

    tokens = estimate_messages_tokens(messages, llm=dummy_llm)
    # 应为 base (5000 + 200) + 新增 tool 消息的快速估算
    assert 5200 < tokens < 5250


def test_online_self_calibration():
    """测试多模型分词自适应校准：DeepSeek/中文高压缩率场景自动平滑学习"""
    from rove.llm import Usage
    dummy_llm = DummyLLM()
    assert dummy_llm.cjk_token_ratio == 1.3

    # 模拟 DeepSeek 返回 120 个汉字，消耗 85 个 output tokens（比例约 0.70）
    response = LLMResponse(
        content="这是一段由大语言模型生成的测试数据，专门用来测试中文字符在分词器中的实际消耗比例。" * 3,
        tool_calls=[],
        model="deepseek-chat",
        stop_reason="end_turn",
        usage=Usage(input_tokens=1000, output_tokens=85),
    )
    dummy_llm._record_usage(response)

    # 验证自适应学习已自动拉低 cjk_token_ratio
    assert dummy_llm.cjk_token_ratio < 1.1

    # 再次返回一轮类似消耗
    dummy_llm._record_usage(response)
    # 比例进一步平滑收敛向 0.7~0.8 附近
    assert dummy_llm.cjk_token_ratio < 0.95


def test_user_anchor_preservation():
    """测试 L1 消息剪枝中的【用户指令锚点（User Pinning）】机制：
    中途用户修改需求的指令绝对不能被剪除。
    """
    messages = [
        Message(role="user", content="请帮我写一个数据清洗系统"),
        Message(role="assistant", content="好的，我先查看目录结构"),
        Message(role="tool", tool_call_id="call_0", content="file1.py\nfile2.py"),
    ]

    # 构造中间 50 轮工具与推理交互
    for i in range(1, 50):
        if i == 20:
            # 用户在中途发出了关键的需求变更与纠错锚点
            messages.append(Message(role="user", content="等等！不要用 MySQL，全部改用 PostgreSQL 重构表结构！"))
        else:
            messages.append(Message(
                role="assistant",
                content=f"Step {i}: 正在分析表结构",
                tool_calls=[ToolCall(tool_id=f"call_{i}", tool_name="bash", tool_args={"cmd": f"check_{i}"})]
            ))
            messages.append(Message(
                role="tool",
                tool_call_id=f"call_{i}",
                content=f"Output for step {i}: " + "x" * 200
            ))

    # 加上最近活跃尾部交互（35条）
    for j in range(50, 70):
        messages.append(Message(
            role="assistant",
            content=f"Tail step {j}",
            tool_calls=[ToolCall(tool_id=f"call_{j}", tool_name="edit", tool_args={"path": "postgres.sql"})]
        ))
        messages.append(Message(role="tool", tool_call_id=f"call_{j}", content="Success"))

    total_orig = len(messages)
    # 触发剪枝
    snipped = snip_compact(messages)

    # 剪枝后消息总数显著减少
    assert len(snipped) < total_orig

    # 核心断言：中途第 20 轮插入的真实用户需求变更必须完整存在！
    user_contents = [m.content for m in snipped if m.role == "user"]
    assert any("等等！不要用 MySQL，全部改用 PostgreSQL" in c for c in user_contents)

    # 验证头部和尾部也都在
    assert "请帮我写一个数据清洗系统" in user_contents[0]
    assert any("Tail step 69" in (m.content or "") for m in snipped)


def test_l2_micro_compact_anti_loop_and_threshold():
    """测试 L2 机制：
    1. 正常代码（<=4000字符）绝对不压缩
    2. 超过4000字符的旧工具结果截断为首尾切片
    3. 严禁出现 'Re-run if needed' 阻断死循环
    4. 最近 10 轮完好保留
    """
    messages = [Message(role="user", content="分析代码库")]

    # 构造 15 轮工具调用
    for i in range(15):
        messages.append(Message(
            role="assistant",
            tool_calls=[ToolCall(tool_id=f"call_{i}", tool_name="read", tool_args={"file": f"f_{i}.py"})]
        ))
        # 前 5 轮中，第 0 轮是 3000 字符的正常代码，第 1 轮是 7000 字符的超长输出
        if i == 0:
            content = "def test_func():\n    pass\n" * 150  # 约 3600 字符
        elif i == 1:
            content = "LOG_LINE_DATA: " + "A" * 7000  # 7000+ 字符
        else:
            content = f"Normal output {i}"
        messages.append(Message(role="tool", tool_call_id=f"call_{i}", content=content))

    # 触发 L2
    compacted = micro_compact(messages)

    tool_msgs = [m for m in compacted if m.role == "tool"]
    assert len(tool_msgs) == 15

    # 第 0 轮（虽然在 10 轮之外，但 <=4000 字符）完全不被压缩
    assert "def test_func():" in tool_msgs[0].content
    assert "[Output truncated" not in tool_msgs[0].content

    # 第 1 轮（>10轮之外且 >4000 字符）被首尾切片压缩
    assert "[Output truncated" in tool_msgs[1].content
    assert "LOG_LINE_DATA:" in tool_msgs[1].content  # 保留了头部

    # 核心断言：严禁包含诱导重新运行的短语
    for m in tool_msgs:
        assert "Re-run if needed" not in (m.content or "")

    # 最近 10 轮工具（索引 5 到 14）必须毫发无损
    for i in range(5, 15):
        assert tool_msgs[i].content == f"Normal output {i}"


def test_persisted_output_protection(tmp_path):
    """测试已持久化的 <persisted-output> 永远不会被后续的 L2/L1 抹除路径"""
    # 模拟 L3 写入持久化输出
    fake_path = tmp_path / "big_output.txt"
    fake_path.write_text("very large content" * 1000)

    persisted_content = (
        f"<persisted-output>\n"
        f"Full output path : {str(fake_path)}\n"
        f"Total size : 18000 字符\n"
        f"Preview : head content ... tail content\n"
        f"</persisted-output>"
    )

    messages = [
        Message(role="user", content="开始分析"),
        Message(role="assistant", tool_calls=[ToolCall(tool_id="c_persist", tool_name="run", tool_args={})]),
        Message(role="tool", tool_call_id="c_persist", content=persisted_content),
    ]

    # 追加 12 轮普通调用，使得上述持久化输出滑入 10 轮之外
    for i in range(12):
        messages.append(Message(role="assistant", tool_calls=[ToolCall(tool_id=f"c_{i}", tool_name="step", tool_args={})]))
        messages.append(Message(role="tool", tool_call_id=f"c_{i}", content=f"Step {i} done"))

    # 执行 L2 微压缩
    compacted = micro_compact(messages)

    # 验证该持久化工具消息依然完整保留了 <persisted-output> 和实际路径
    first_tool = [m for m in compacted if m.tool_call_id == "c_persist"][0]
    assert "<persisted-output>" in first_tool.content
    assert str(fake_path) in first_tool.content


def test_tool_pairing_integrity():
    """测试剪枝时严格保证 tool_calls 与 tool_result 的配对完整性，且 adapter 转换无相邻 user 角色"""
    messages = [
        Message(role="user", content="开始测试配对"),
        Message(
            role="assistant",
            content="执行多工具并发",
            tool_calls=[
                ToolCall(tool_id="call_a", tool_name="bash", tool_args={"cmd": "ls"}),
                ToolCall(tool_id="call_b", tool_name="bash", tool_args={"cmd": "pwd"}),
            ]
        ),
        Message(role="tool", tool_call_id="call_a", content="dir1 dir2"),
        Message(role="tool", tool_call_id="call_b", content="/home/workspace"),
    ]

    # 补充更多消息触发剪枝
    for i in range(70):
        messages.append(Message(
            role="assistant",
            content=f"thinking {i}",
            tool_calls=[ToolCall(tool_id=f"tc_{i}", tool_name="read", tool_args={"id": i})]
        ))
        messages.append(Message(role="tool", tool_call_id=f"tc_{i}", content=f"content {i}"))

    snipped = snip_compact(messages)

    # 用 Anthropic 适配器转换消息
    converted = AnthropicLLMAdapter._convert_messages(snipped)

    # 断言转换后的列表必须交替（没有两个连续的相同角色）
    for i in range(len(converted) - 1):
        assert converted[i]["role"] != converted[i + 1]["role"], f"Found adjacent identical roles at {i}"


def test_adaptive_watermarks_1m_vs_200k():
    """测试 1M 窗口 vs 200k 窗口的动态自适应水位线效果"""
    messages = [Message(role="user", content="测试")]

    # 构造约 50k tokens 的消息量
    for i in range(20):
        messages.append(Message(role="assistant", tool_calls=[ToolCall(tool_id=f"c_{i}", tool_name="t", tool_args={})]))
        messages.append(Message(role="tool", tool_call_id=f"c_{i}", content="DATA LINE " * 800))  # 每条约 8000 字符

    tokens = estimate_messages_tokens(messages)
    assert 30_000 < tokens < 80_000

    # 1. 在 1M 窗口下：10% 水位为 100k，50k tokens 属于正常区间，L2 不应该被触发
    dummy_1m = DummyLLM(context_window=1_000_000)
    msg_copy_1m = [Message(role=m.role, content=m.content, tool_calls=m.tool_calls, tool_call_id=m.tool_call_id) for m in messages]
    result_1m = run_pipeline(dummy_1m, msg_copy_1m)
    # 验证没有一条消息被截断
    assert all("[Output truncated" not in (m.content or "") for m in result_1m)

    # 2. 在 200k 窗口下（如 Haiku）：10% 水位为 20k，50k tokens 超出了水位线，L2 必须主动触发！
    dummy_200k = DummyLLM(context_window=200_000)
    msg_copy_200k = [Message(role=m.role, content=m.content, tool_calls=m.tool_calls, tool_call_id=m.tool_call_id) for m in messages]
    result_200k = run_pipeline(dummy_200k, msg_copy_200k)
    # 验证超标工具结果被成功切片脱水
    assert any("[Output truncated" in (m.content or "") for m in result_200k)


def test_l4_compact_history_retains_tail_memory():
    """测试 L4 深度摘要压缩时，保留尾部最近 5~8 条活跃交互，防止断崖式失忆"""
    dummy_llm = DummyLLM()
    messages = [
        Message(role="user", content="从头开始排查性能问题"),
    ]
    for i in range(20):
        messages.append(Message(role="assistant", content=f"Step {i}: checking component {i}"))
        messages.append(Message(role="tool", tool_call_id=f"t_{i}", content=f"Metric {i}: ok"))

    # 最新正在编辑的代码（活跃工作区）
    messages.append(Message(
        role="assistant",
        content="我正在重构 fast_cache.py 的关键方法",
        tool_calls=[ToolCall(tool_id="t_curr", tool_name="edit", tool_args={"file": "fast_cache.py"})]
    ))
    messages.append(Message(role="tool", tool_call_id="t_curr", content="Cache updated successfully"))

    compacted = compact_history(dummy_llm, messages)

    # 验证首条是全局摘要
    assert "[对话已压缩]" in compacted[0].content
    assert "核心诉求与关键发现" in compacted[0].content

    # 验证尾部的活跃工作记忆完好保留（包含正在重构 fast_cache.py 的关键上下文）
    tail_contents = [m.content or "" for m in compacted[1:]]
    assert any("fast_cache.py" in c for c in tail_contents)
    assert any("Cache updated successfully" in c for c in tail_contents)


def test_run_pipeline_ordering(tmp_path):
    """测试流水线次序：L3 -> L2 -> L1 -> L4 正确按序流转"""
    dummy_llm = DummyLLM(context_window=200_000)

    # 构造包含超大当轮输出与多轮历史的消息
    messages = [
        Message(role="user", content="开始流水线测试"),
    ]
    for i in range(30):
        messages.append(Message(role="assistant", tool_calls=[ToolCall(tool_id=f"c_{i}", tool_name="cmd", tool_args={})]))
        messages.append(Message(role="tool", tool_call_id=f"c_{i}", content=f"step {i} " + "X" * 1000))

    # 最后一轮是一个超大输出（> TOOL_RESULT_BUDGET 200k），验证 L3 能够捕获
    messages.append(Message(role="assistant", tool_calls=[ToolCall(tool_id="huge_call", tool_name="cat", tool_args={})]))
    messages.append(Message(role="tool", tool_call_id="huge_call", content="HUGE_DATA\n" * 25000))

    # 运行流水线
    processed = run_pipeline(dummy_llm, messages)

    # 验证 L3 成功将当轮超大输出持久化为 <persisted-output>
    huge_msg = [m for m in processed if m.tool_call_id == "huge_call"][0]
    assert "<persisted-output>" in huge_msg.content
    assert "HUGE_DATA" in huge_msg.content
