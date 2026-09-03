import { useEffect, useRef, useState, useCallback } from 'react';
import { ChatMessage, ToolStep, ApprovalRequest } from '../types/rove';

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

  const wsRef = useRef<WebSocket | null>(null);

  // 清空本轮临时的流式数据
  const resetStreamingState = useCallback(() => {
    setCurrentStreamingText('');
    setActiveToolSteps([]);
    setPendingApproval(null);
    setIsRunning(false);
  }, []);

  useEffect(() => {
    if (!sessionId) {
      setMessages([]);
      resetStreamingState();
      return;
    }

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.host;
    // 开发环境下 Vite proxy 会代理 /ws，生产环境下与后端同域
    const wsUrl = `${protocol}//${host}/ws/${sessionId}`;

    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      setIsConnected(true);
      resetStreamingState();
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

          case 'approval_required':
            setPendingApproval({
              approval_id: data.approval_id,
              tool_name: data.tool_name,
              arguments: data.arguments,
              reason: data.reason,
            });
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
            if (onSessionUpdated) {
              onSessionUpdated();
            }
            break;

          case 'compact_done':
            if (ws.readyState === WebSocket.OPEN) {
              ws.send(JSON.stringify({ type: 'get_history' }));
            }
            if (onSessionUpdated) {
              onSessionUpdated();
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
      setIsConnected(false);
      setIsRunning(false);
    };

    ws.onerror = (err) => {
      console.error('WebSocket error:', err);
      setIsConnected(false);
      setIsRunning(false);
    };

    return () => {
      ws.close();
      wsRef.current = null;
    };
  }, [sessionId, onSessionUpdated, resetStreamingState]);

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

  const sendApproval = useCallback((approvalId: string, decision: 'y' | 's' | 'N') => {
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

  return {
    isConnected,
    messages,
    currentStreamingText,
    activeToolSteps,
    pendingApproval,
    isRunning,
    sendMessage,
    sendApproval,
    sendCompact,
  };
}
