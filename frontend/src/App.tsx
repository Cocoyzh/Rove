import { useState, useEffect, useCallback } from 'react';
import { Sidebar } from './components/Sidebar';
import { ChatArea } from './components/ChatArea';
import { CollabPanel } from './components/CollabPanel';
import { ApprovalCard } from './components/ApprovalCard';
import { useRoveWebSocket } from './hooks/useRoveWebSocket';
import { SessionSummary, SystemStatus } from './types/rove';

export default function App() {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [currentSessionId, setCurrentSessionId] = useState<string | null>(null);
  const [systemStatus, setSystemStatus] = useState<SystemStatus | null>(null);
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(false);
  const [isCollabOpen, setIsCollabOpen] = useState(false);

  // 获取所有会话列表
  const fetchSessions = useCallback(async () => {
    try {
      const res = await fetch('/api/sessions');
      if (res.ok) {
        const data: SessionSummary[] = await res.json();
        setSessions(data);
        if (data.length > 0) {
          setCurrentSessionId((prev) => {
            if (prev && data.some((s) => s.id === prev)) {
              return prev;
            }
            return data[0].id;
          });
        }
      }
    } catch (err) {
      console.error('Failed to fetch sessions:', err);
    }
  }, []);

  // 获取系统状态（传入 session_id 获取当前会话专属上下文与消耗）
  const fetchStatus = useCallback(async (targetSessionId?: string | null) => {
    try {
      const sid = targetSessionId !== undefined ? targetSessionId : currentSessionId;
      const url = sid ? `/api/system/status?session_id=${encodeURIComponent(sid)}` : '/api/system/status';
      const res = await fetch(url);
      if (res.ok) {
        const data: SystemStatus = await res.json();
        setSystemStatus(data);
      }
    } catch (err) {
      console.error('Failed to fetch system status:', err);
    }
  }, [currentSessionId]);

  useEffect(() => {
    fetchSessions();
    fetchStatus(currentSessionId);
    const timer = setInterval(() => fetchStatus(), 5000);
    return () => clearInterval(timer);
  }, [fetchSessions, fetchStatus, currentSessionId]);

  const handleSessionUpdated = useCallback(() => {
    fetchSessions();
    fetchStatus(currentSessionId);
  }, [fetchSessions, fetchStatus, currentSessionId]);

  // WebSocket 实时连接管理
  const {
    isConnected,
    messages,
    currentStreamingText,
    activeToolSteps,
    pendingApproval,
    isRunning,
    sendMessage,
    sendApproval,
    sendCompact,
    queueInitialMessage,
  } = useRoveWebSocket({
    sessionId: currentSessionId,
    onSessionUpdated: handleSessionUpdated,
  });

  // 创建新会话
  const handleCreateSession = async () => {
    try {
      const res = await fetch('/api/sessions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: '新会话' }),
      });
      if (res.ok) {
        const newSession = await res.json();
        setSessions((prev) => [newSession, ...prev]);
        setCurrentSessionId(newSession.id);
      }
    } catch (err) {
      console.error('Failed to create session:', err);
    }
  };

  // 统一消息发送处理：无会话时自动新建会话并无感发送
  const handleSendMessage = async (query: string) => {
    if (!currentSessionId) {
      try {
        const title = query.slice(0, 20).trim() || '新会话';
        const res = await fetch('/api/sessions', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ title }),
        });
        if (res.ok) {
          const newSession = await res.json();
          queueInitialMessage(query);
          setSessions((prev) => [newSession, ...prev]);
          setCurrentSessionId(newSession.id);
        }
      } catch (err) {
        console.error('Failed to auto create session on send:', err);
      }
    } else {
      sendMessage(query);
    }
  };

  // 删除会话（支持彻底清空所有会话）
  const handleDeleteSession = async (id: string) => {
    try {
      const res = await fetch(`/api/sessions/${id}`, { method: 'DELETE' });
      if (res.ok) {
        setSessions((prev) => {
          const next = prev.filter((s) => s.id !== id);
          if (currentSessionId === id) {
            setCurrentSessionId(next.length > 0 ? next[0].id : null);
          }
          return next;
        });
      }
    } catch (err) {
      console.error('Failed to delete session:', err);
    }
  };

  // 重命名会话
  const handleRenameSession = async (id: string, newTitle: string) => {
    try {
      const res = await fetch(`/api/sessions/${id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: newTitle }),
      });
      if (res.ok) {
        setSessions((prev) =>
          prev.map((s) => (s.id === id ? { ...s, title: newTitle } : s))
        );
      }
    } catch (err) {
      console.error('Failed to rename session:', err);
    }
  };

  const currentSession = sessions.find((s) => s.id === currentSessionId);

  return (
    <div className="flex h-screen w-screen bg-white text-slate-700 overflow-hidden font-sans">
      {/* 1. 左侧栏：会话管理与系统状态 (平滑渐变折叠) */}
      <Sidebar
        sessions={sessions}
        currentSessionId={currentSessionId}
        onSelectSession={setCurrentSessionId}
        onCreateSession={handleCreateSession}
        onDeleteSession={handleDeleteSession}
        onRenameSession={handleRenameSession}
        systemStatus={systemStatus}
        onTriggerCompact={sendCompact}
        isCollapsed={isSidebarCollapsed}
        onToggleCollapse={() => setIsSidebarCollapsed((prev) => !prev)}
      />

      {/* 2. 中间主对话控制台 */}
      <ChatArea
        sessionTitle={currentSession?.title || '新会话'}
        hasActiveSession={!!currentSessionId}
        isConnected={isConnected}
        messages={messages}
        currentStreamingText={currentStreamingText}
        activeToolSteps={activeToolSteps}
        pendingApproval={pendingApproval}
        isRunning={isRunning}
        onSendMessage={handleSendMessage}
        onSendApproval={sendApproval}
        isSidebarCollapsed={isSidebarCollapsed}
        onToggleSidebar={() => setIsSidebarCollapsed((prev) => !prev)}
        isCollabOpen={isCollabOpen}
        onToggleCollab={() => setIsCollabOpen((prev) => !prev)}
        renderApprovalCard={(appr) => (
          <ApprovalCard approval={appr} onRespond={sendApproval} />
        )}
      />

      {/* 3. 右侧栏：Task 看板与 Teammates 协同监控 (平滑渐变折叠) */}
      <CollabPanel
        isOpen={isCollabOpen}
        onClose={() => setIsCollabOpen(false)}
      />
    </div>
  );
}
