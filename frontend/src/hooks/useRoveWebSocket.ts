import { useEffect, useRef, useState, useCallback } from 'react';
import { ChatMessage, ToolStep, ApprovalRequest, ApprovalMode, CallEvent, SubagentRun } from '../types/rove';

interface UseRoveWebSocketOptions {
  sessionId: string | null;
  onSessionUpdated?: () => void;
}

export function useRoveWebSocket({ sessionId, onSessionUpdated }: UseRoveWebSocketOptions) {
  const [isConnected, setIsConnected] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [currentStreamingText, setCurrentStreamingText] = useState('');
  const [activeToolSteps, setActiveToolSteps] = useState<ToolStep[]>([]);
  const [pendingApproval, setPendingApproval] = useState<ApprovalRequest | null>(null);
  const [isRunning, setIsRunning] = useState(false);
  const [approvalMode, setApprovalMode] = useState<ApprovalMode>('workspace');
  // 调用检查器数据：切换会话时从 /api/traces 回放历史，实时 call_event 按 seq upsert
  const [callEvents, setCallEvents] = useState<CallEvent[]>([]);
  const [subagents, setSubagents] = useState<SubagentRun[]>([]);

  const wsRef = useRef<WebSocket | null>(null);
  const pendingQueryRef = useRef<string | null>(null);
  const onSessionUpdatedRef = useRef(onSessionUpdated);
  useEffect(() => {
    onSessionUpdatedRef.current = onSessionUpdated;
  }, [onSessionUpdated]);

  // 清空本轮临时的流式数据
  const resetStreamingState = useCallback(() => {
    setCurrentStreamingText('');
    setActiveToolSteps([]);
    setPendingApproval(null);
    setIsRunning(false);
    setSubagents([]);
  }, []);

  // call_event 按 seq upsert（running 事件先占位，终态事件覆盖同 seq）
  const upsertCallEvent = useCallback((event: CallEvent) => {
    setCallEvents((prev) => {
      const idx = prev.findIndex((e) => e.seq === event.seq);
      if (idx >= 0) {
        const next = [...prev];
        next[idx] = event;
        return next;
      }
      return [...prev, event].sort((a, b) => a.seq - b.seq);
    });
  }, []);

  // 从 trace 文件回放该会话的历史调用（重开页面 / 断线重连后补齐）
  const fetchCallEvents = useCallback((sid: string) => {
    fetch(`/api/traces/${encodeURIComponent(sid)}`)
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (data && Array.isArray(data.events)) {
          setCallEvents(data.events as CallEvent[]);
        }
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (!sessionId) {
      setIsConnected(false);
      setMessages([]);
      setCallEvents([]);
      resetStreamingState();
      return;
    }

    fetchCallEvents(sessionId);

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.host;
    // 开发环境下 Vite proxy 会代理 /ws，生产环境下与后端同域
    const wsUrl = `${protocol}//${host}/ws/${sessionId}`;

    let isCleanedUp = false;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

    const connect = () => {
      if (isCleanedUp) return;

      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        if (isCleanedUp) return;
        setIsConnected(true);
        resetStreamingState();
        // 重连可能错过事件，补拉一次 trace 回放
        fetchCallEvents(sessionId);

        // 若有待发送的首次消息，在连接建立完成后立即发出
        if (pendingQueryRef.current) {
          const q = pendingQueryRef.current;
          pendingQueryRef.current = null;
          setMessages([{ role: 'user', content: q }]);
          setIsRunning(true);
          ws.send(JSON.stringify({ type: 'chat', query: q }));
        }
      };

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          switch (data.type) {
            case 'history':
              setMessages(data.messages || []);
              break;

            case 'token':
              setIsRunning(true);
              setCurrentStreamingText((prev) => prev + data.content);
              break;

            case 'call_event':
              upsertCallEvent(data.event as CallEvent);
              break;

            case 'session_title':
              // 旁路命名完成，立即刷新侧栏标题
              if (onSessionUpdatedRef.current) {
                onSessionUpdatedRef.current();
              }
              break;

            case 'tool_start':
              setIsRunning(true);
              setActiveToolSteps((prev) => [
                ...prev,
                {
                  tool_id: data.tool_id,
                  tool_name: data.tool_name,
                  tool_args: data.tool_args,
                  status: 'running',
                },
              ]);
              break;

            case 'tool_end':
              setActiveToolSteps((prev) =>
                prev.map((step) =>
                  step.tool_id === data.tool_id
                    ? {
                        ...step,
                        output: data.output,
                        is_error: data.is_error,
                        cost_ms: data.cost_ms,
                        status: data.is_error ? 'error' : 'completed',
                      }
                    : step
                )
              );
              break;

            case 'subagent_status':
              // SubAgent 起止/进度心跳：维护最近几条运行态供 TeamMonitor 展示
              setSubagents((prev) => {
                const next = prev.filter((s) => s.task_id !== data.task_id);
                next.unshift({
                  task_id: data.task_id,
                  status: data.status,
                  objective: data.objective,
                  turn: data.turn,
                  max_turns: data.max_turns,
                  activity: data.activity,
                  total_tokens: data.total_tokens,
                });
                return next.slice(0, 5);
              });
              break;

            case 'approval_required':
              setPendingApproval({
                approval_id: data.approval_id,
                tool_name: data.tool_name,
                arguments: data.arguments,
                reason: data.reason,
                suggested_prefix: data.suggested_prefix,
                target_path: data.target_path,
                available_choices: data.available_choices,
              });
              break;

            case 'approval_mode_changed':
              setApprovalMode(data.mode as ApprovalMode);
              break;

            case 'error':
              setIsRunning(false);
              setMessages((prev) => [
                ...prev,
                {
                  role: 'assistant',
                  content: `⚠️ **模型服务连接或执行异常**\n\n\`\`\`text\n${data.error || 'Unknown error'}\n\`\`\`\n\n> 💡 **排查建议**：\n> 1. 检查根目录下 \`.env\` 的 \`LLM_API_KEY\`、\`LLM_BASE_URL\`、\`LLM_MODEL_ID\`。\n> 2. 检查代理与网络是否能正常访问模型端点。`,
                },
              ]);
              break;

            case 'done':
              setIsRunning(false);
              setCurrentStreamingText('');
              setActiveToolSteps([]);
              setPendingApproval(null);
              // 重新请求获取完整历史
              if (ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({ type: 'get_history' }));
              }
              if (onSessionUpdatedRef.current) {
                onSessionUpdatedRef.current();
              }
              break;

            case 'compact_done':
              if (ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({ type: 'get_history' }));
              }
              if (onSessionUpdatedRef.current) {
                onSessionUpdatedRef.current();
              }
              break;

            default:
              break;
          }
        } catch (err) {
          console.error('Failed to parse WebSocket message:', err);
        }
      };

      ws.onclose = () => {
        if (isCleanedUp) return;
        setIsConnected(false);
        setIsRunning(false);
        // 服务端若重启或网络临时抖动，2秒后尝试自动重连
        reconnectTimer = setTimeout(connect, 2000);
      };

      ws.onerror = (err) => {
        if (isCleanedUp) return;
        console.error('WebSocket error:', err);
        setIsConnected(false);
        setIsRunning(false);
      };
    };

    connect();

    return () => {
      isCleanedUp = true;
      if (reconnectTimer) {
        clearTimeout(reconnectTimer);
      }
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, [sessionId, resetStreamingState]);

  const sendMessage = useCallback((query: string) => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
      return false;
    }
    // 乐观将用户消息立即追加到本地
    setMessages((prev) => [...prev, { role: 'user', content: query }]);
    setIsRunning(true);
    setCurrentStreamingText('');
    setActiveToolSteps([]);
    setPendingApproval(null);

    wsRef.current.send(JSON.stringify({ type: 'chat', query }));
    return true;
  }, []);

  const sendApproval = useCallback((approvalId: string, decision: string) => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
      return false;
    }
    setPendingApproval(null);
    wsRef.current.send(
      JSON.stringify({
        type: 'approval_response',
        approval_id: approvalId,
        decision,
      })
    );
    return true;
  }, []);

  const sendCompact = useCallback(() => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
      return false;
    }
    wsRef.current.send(JSON.stringify({ type: 'compact' }));
    return true;
  }, []);

  const changeApprovalMode = useCallback((mode: ApprovalMode) => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
      return false;
    }
    wsRef.current.send(JSON.stringify({ type: 'set_approval_mode', mode }));
    return true;
  }, []);

  const queueInitialMessage = useCallback((query: string) => {
    pendingQueryRef.current = query;
  }, []);

  return {
    isConnected,
    messages,
    currentStreamingText,
    activeToolSteps,
    pendingApproval,
    isRunning,
    approvalMode,
    callEvents,
    subagents,
    sendMessage,
    sendApproval,
    sendCompact,
    changeApprovalMode,
    queueInitialMessage,
  };
}
