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
  const [isCollabOpen, setIsCollabOpen] = useState(true);

  // 获取所有会话列表
  const fetchSessions = useCallback(async () => {
    try {
      const res = await fetch('/api/sessions');
      if (res.ok) {
        const data: SessionSummary[] = await res.json();
        setSessions(data);
        if (data.length > 0 && !currentSessionId) {
          setCurrentSessionId(data[0].id);
        }
      }
    } catch (err) {
      console.error('Failed to fetch sessions:', err);
    }
  }, [currentSessionId]);

  // 获取系统状态
  const fetchStatus = useCallback(async () => {
    try {
      const res = await fetch('/api/system/status');
      if (res.ok) {
        const data: SystemStatus = await res.json();
        setSystemStatus(data);
      }
    } catch (err) {
      console.error('Failed to fetch system status:', err);
    }
  }, []);

  useEffect(() => {
    fetchSessions();
    fetchStatus();
    const timer = setInterval(fetchStatus, 5000);
    return () => clearInterval(timer);
  }, [fetchSessions, fetchStatus]);

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
  } = useRoveWebSocket({
    sessionId: currentSessionId,
    onSessionUpdated: () => {
      fetchSessions();
      fetchStatus();
    },
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

  // 删除会话
  const handleDeleteSession = async (id: string) => {
    try {
      const res = await fetch(`/api/sessions/${id}`, { method: 'DELETE' });
      if (res.ok) {
        setSessions((prev) => prev.filter((s) => s.id !== id));
        if (currentSessionId === id) {
          const remaining = sessions.filter((s) => s.id !== id);
          setCurrentSessionId(remaining.length > 0 ? remaining[0].id : null);
        }
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
    <div className="flex h-screen w-screen bg-rove-bg text-rove-text overflow-hidden font-sans">
      {/* 1. 左侧栏：会话管理与系统状态 */}
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
        isConnected={isConnected}
        messages={messages}
        currentStreamingText={currentStreamingText}
        activeToolSteps={activeToolSteps}
        pendingApproval={pendingApproval}
        isRunning={isRunning}
        onSendMessage={sendMessage}
        onSendApproval={sendApproval}
        isCollabOpen={isCollabOpen}
        onToggleCollab={() => setIsCollabOpen((prev) => !prev)}
        renderApprovalCard={(appr) => (
          <ApprovalCard approval={appr} onRespond={sendApproval} />
        )}
      />

      {/* 3. 右侧栏：Task 看板与 Teammates 协同监控 */}
      <CollabPanel
        isOpen={isCollabOpen}
        onClose={() => setIsCollabOpen(false)}
      />
    </div>
  );
}
