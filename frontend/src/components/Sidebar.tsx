import React, { useState } from 'react';
import {
  Plus,
  MessageSquare,
  Trash2,
  Cpu,
  Archive,
  Terminal,
  Activity,
  ChevronLeft,
  ChevronRight,
  Check,
  X,
  Edit2,
} from 'lucide-react';
import { SessionSummary, SystemStatus } from '../types/rove';

interface SidebarProps {
  sessions: SessionSummary[];
  currentSessionId: string | null;
  onSelectSession: (id: string) => void;
  onCreateSession: () => void;
  onDeleteSession: (id: string) => void;
  onRenameSession: (id: string, newTitle: string) => void;
  systemStatus: SystemStatus | null;
  onTriggerCompact: () => void;
  isCollapsed: boolean;
  onToggleCollapse: () => void;
}

export const Sidebar: React.FC<SidebarProps> = ({
  sessions,
  currentSessionId,
  onSelectSession,
  onCreateSession,
  onDeleteSession,
  onRenameSession,
  systemStatus,
  onTriggerCompact,
  isCollapsed,
  onToggleCollapse,
}) => {
  const [editingSessionId, setEditingSessionId] = useState<string | null>(null);
  const [editingTitle, setEditingTitle] = useState('');

  const startRename = (s: SessionSummary, e: React.MouseEvent) => {
    e.stopPropagation();
    setEditingSessionId(s.id);
    setEditingTitle(s.title);
  };

  const confirmRename = (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (editingTitle.trim()) {
      onRenameSession(id, editingTitle.trim());
    }
    setEditingSessionId(null);
  };

  const cancelRename = (e: React.MouseEvent) => {
    e.stopPropagation();
    setEditingSessionId(null);
  };

  if (isCollapsed) {
    return (
      <div className="w-14 h-full bg-rove-sidebar border-r border-rove-border flex flex-col items-center py-4 justify-between transition-all duration-300">
        <div className="flex flex-col items-center space-y-4">
          <button
            onClick={onToggleCollapse}
            className="p-2 hover:bg-rove-card rounded-lg text-rove-textDim hover:text-rove-cyan transition-colors"
            title="展开侧边栏"
          >
            <ChevronRight size={18} />
          </button>
          <button
            onClick={onCreateSession}
            className="p-2.5 bg-rove-card hover:bg-rove-border rounded-lg text-rove-cyan transition-colors"
            title="新建会话"
          >
            <Plus size={18} />
          </button>
        </div>
        <div className="flex flex-col items-center space-y-3 text-rove-textDim">
          <button
            onClick={onTriggerCompact}
            className="p-2 hover:bg-rove-card rounded-lg hover:text-rove-yellow transition-colors"
            title="立即压缩上下文 (/compact)"
          >
            <Archive size={16} />
          </button>
        </div>
      </div>
    );
  }

  const contextPct = systemStatus?.context_used_pct || 0;
  const pctColor =
    contextPct > 80 ? 'bg-rove-red' : contextPct > 50 ? 'bg-rove-yellow' : 'bg-rove-cyan';

  return (
    <aside className="w-72 h-full bg-rove-sidebar border-r border-rove-border flex flex-col justify-between transition-all duration-300 select-none">
      {/* 顶部标题栏与新建按钮 */}
      <div className="p-4 border-b border-rove-border">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center space-x-2.5">
            <div className="w-8 h-8 rounded-lg bg-rove-card border border-rove-border flex items-center justify-center text-rove-cyan shadow-[0_0_12px_rgba(34,211,238,0.15)]">
              <Terminal size={18} />
            </div>
            <div>
              <span className="font-mono font-bold tracking-wider text-rove-textBright text-base">
                ROVE
              </span>
              <span className="text-[10px] ml-1.5 px-1.5 py-0.5 rounded bg-cyan-950/60 border border-cyan-800/40 text-rove-cyan font-mono">
                v0.1
              </span>
            </div>
          </div>
          <button
            onClick={onToggleCollapse}
            className="p-1.5 hover:bg-rove-card rounded-md text-rove-textDim hover:text-rove-text transition-colors"
            title="收起侧边栏"
          >
            <ChevronLeft size={16} />
          </button>
        </div>

        <button
          onClick={onCreateSession}
          className="w-full py-2 px-3 rounded-lg border border-rove-cyan/30 bg-rove-cyan/10 hover:bg-rove-cyan/20 text-rove-cyan flex items-center justify-center space-x-2 text-sm font-medium transition-all shadow-[0_0_15px_rgba(34,211,238,0.08)]"
        >
          <Plus size={16} />
          <span>新会话 (New Chat)</span>
        </button>
      </div>

      {/* 会话历史列表 */}
      <div className="flex-1 overflow-y-auto px-2 py-3 space-y-1">
        <div className="px-2 pb-1 text-[11px] font-mono text-rove-textDim uppercase tracking-wider">
          Sessions
        </div>
        {sessions.length === 0 ? (
          <div className="text-center py-8 text-xs text-rove-textDim">暂无会话历史</div>
        ) : (
          sessions.map((s) => {
            const isActive = s.id === currentSessionId;
            const isEditing = editingSessionId === s.id;

            return (
              <div
                key={s.id}
                onClick={() => !isEditing && onSelectSession(s.id)}
                className={`group relative flex items-center justify-between px-3 py-2.5 rounded-lg text-sm cursor-pointer transition-colors ${
                  isActive
                    ? 'bg-rove-card text-rove-cyan font-medium border border-rove-border'
                    : 'text-rove-text hover:bg-rove-card/50 hover:text-rove-textBright'
                }`}
              >
                <div className="flex items-center space-x-2.5 min-w-0 flex-1">
                  <MessageSquare
                    size={15}
                    className={isActive ? 'text-rove-cyan' : 'text-rove-textDim group-hover:text-rove-text'}
                  />
                  {isEditing ? (
                    <input
                      type="text"
                      value={editingTitle}
                      onChange={(e) => setEditingTitle(e.target.value)}
                      onClick={(e) => e.stopPropagation()}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') confirmRename(s.id, e as any);
                        if (e.key === 'Escape') cancelRename(e as any);
                      }}
                      autoFocus
                      className="bg-rove-bg border border-rove-cyan/60 rounded px-1.5 py-0.5 text-xs text-rove-textBright outline-none w-36 font-sans"
                    />
                  ) : (
                    <span className="truncate text-xs">{s.title}</span>
                  )}
                </div>

                {/* 悬停操作按钮 */}
                <div className="flex items-center space-x-1 opacity-0 group-hover:opacity-100 transition-opacity ml-2">
                  {isEditing ? (
                    <>
                      <button
                        onClick={(e) => confirmRename(s.id, e)}
                        className="p-1 hover:text-rove-green text-rove-textDim"
                        title="确认"
                      >
                        <Check size={13} />
                      </button>
                      <button
                        onClick={cancelRename}
                        className="p-1 hover:text-rove-red text-rove-textDim"
                        title="取消"
                      >
                        <X size={13} />
                      </button>
                    </>
                  ) : (
                    <>
                      <button
                        onClick={(e) => startRename(s, e)}
                        className="p-1 hover:text-rove-cyan text-rove-textDim transition-colors"
                        title="重命名"
                      >
                        <Edit2 size={12} />
                      </button>
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          onDeleteSession(s.id);
                        }}
                        className="p-1 hover:text-rove-red text-rove-textDim transition-colors"
                        title="删除会话"
                      >
                        <Trash2 size={12} />
                      </button>
                    </>
                  )}
                </div>
              </div>
            );
          })
        )}
      </div>

      {/* 底部系统状态指标面板 */}
      <div className="p-3 border-t border-rove-border bg-rove-sidebar/50">
        <div className="rounded-lg bg-rove-card border border-rove-border p-3 space-y-2.5 text-xs">
          <div className="flex items-center justify-between text-rove-textDim">
            <div className="flex items-center space-x-1.5">
              <Cpu size={13} className="text-rove-cyan" />
              <span className="font-mono text-[11px]">MODEL</span>
            </div>
            <span className="font-mono text-rove-textBright truncate max-w-[120px]" title={systemStatus?.model || 'Claude'}>
              {systemStatus?.model ? systemStatus.model.replace('claude-', '') : 'Sonnet'}
            </span>
          </div>

          <div>
            <div className="flex justify-between text-[11px] text-rove-textDim font-mono mb-1">
              <span>CONTEXT</span>
              <span>{contextPct.toFixed(1)}%</span>
            </div>
            <div className="w-full h-1.5 bg-rove-bg rounded-full overflow-hidden border border-rove-border">
              <div
                className={`h-full ${pctColor} transition-all duration-500`}
                style={{ width: `${Math.min(Math.max(contextPct, 2), 100)}%` }}
              />
            </div>
          </div>

          <div className="flex items-center justify-between pt-1 border-t border-rove-border/50 text-[11px] font-mono text-rove-textDim">
            <div className="flex items-center space-x-1">
              <Activity size={12} />
              <span>TOKENS</span>
            </div>
            <span>
              {((systemStatus?.total_input_tokens || 0) + (systemStatus?.total_output_tokens || 0)).toLocaleString()}
            </span>
          </div>

          <button
            onClick={onTriggerCompact}
            className="w-full mt-1 py-1.5 px-2 bg-rove-bg hover:bg-rove-border border border-rove-border rounded text-[11px] font-mono text-rove-textDim hover:text-rove-yellow flex items-center justify-center space-x-1.5 transition-colors"
            title="手动触发四层归档摘要压缩"
          >
            <Archive size={12} />
            <span>/compact 会话归档</span>
          </button>
        </div>
      </div>
    </aside>
  );
};
