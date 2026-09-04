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
  Check,
  X,
  Edit2,
  Compass,
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

  const sessionCtx = systemStatus?.session_context_tokens || 0;
  const contextWindow = systemStatus?.context_window || 1_000_000;
  const contextPct = systemStatus?.context_used_pct || 0;
  const pctColor =
    contextPct > 80 ? 'bg-rose-500' : contextPct > 50 ? 'bg-amber-500' : 'bg-sky-500';

  const formatTokenCount = (num: number): string => {
    if (num >= 1_000_000) {
      return `${(num / 1_000_000).toFixed(num % 1_000_000 === 0 ? 0 : 1)}M`;
    }
    if (num >= 10_000) {
      return `${(num / 1_000).toFixed(0)}k`;
    }
    if (num >= 1_000) {
      return `${(num / 1_000).toFixed(1)}k`;
    }
    return num.toLocaleString();
  };

  const contextPctLabel =
    contextPct === 0
      ? '0%'
      : contextPct < 0.1
      ? '<0.1%'
      : `${contextPct.toFixed(1)}%`;

  return (
    <aside
      className={`h-full bg-slate-50 border-r border-slate-200 flex flex-col justify-between select-none flex-shrink-0 transition-all duration-300 ease-in-out ${
        isCollapsed ? 'w-0 opacity-0 pointer-events-none' : 'w-64 md:w-72 opacity-100'
      } overflow-hidden`}
    >
      {/* 内部固定宽度的容器，保证伸缩动画时内部元素不产生突兀挤压换行 */}
      <div className="w-64 md:w-72 h-full flex flex-col justify-between">
        {/* 顶部标题与新建按钮 */}
        <div className="p-3.5 border-b border-slate-200/80">
          <div className="flex items-center justify-between mb-3 px-1">
            <div className="flex items-center space-x-2.5">
              <div className="w-7 h-7 rounded-lg bg-white border border-slate-200 flex items-center justify-center text-sky-600 shadow-sm">
                <Compass size={17} />
              </div>
              <div className="flex items-baseline space-x-1.5">
                <span className="font-semibold text-slate-900 text-sm tracking-tight">
                  Rove
                </span>
                <span className="text-[11px] text-slate-400 font-normal">
                  Agent Harness
                </span>
              </div>
            </div>
            <button
              onClick={onToggleCollapse}
              className="p-1 hover:bg-slate-200/60 rounded-md text-slate-400 hover:text-slate-700 transition-colors"
              title="收起侧边栏"
            >
              <ChevronLeft size={16} />
            </button>
          </div>

          <button
            onClick={onCreateSession}
            className="w-full py-2 px-3 rounded-xl border border-slate-200 bg-white hover:bg-slate-50 hover:border-slate-300 text-slate-700 hover:text-slate-900 flex items-center justify-center space-x-2 text-xs font-medium shadow-sm transition-all group"
          >
            <Plus size={15} className="text-sky-600 group-hover:scale-110 transition-transform" />
            <span>新建会话</span>
          </button>
        </div>

        {/* 会话列表 */}
        <div className="flex-1 overflow-y-auto px-2 py-3 space-y-1">
          <div className="px-2.5 pb-1.5 text-[11px] font-medium text-slate-400 uppercase tracking-wider">
            对话历史
          </div>
          {sessions.length === 0 ? (
            <div className="text-center py-8 text-xs text-slate-400">暂无会话</div>
          ) : (
            sessions.map((s) => {
              const isActive = s.id === currentSessionId;
              const isEditing = editingSessionId === s.id;

              return (
                <div
                  key={s.id}
                  onClick={() => !isEditing && onSelectSession(s.id)}
                  className={`group relative flex items-center justify-between px-3 py-2.5 rounded-xl text-xs cursor-pointer transition-all ${
                    isActive
                      ? 'bg-white text-sky-700 font-medium shadow-sm border border-slate-200'
                      : 'text-slate-600 hover:bg-slate-200/50 hover:text-slate-900'
                  }`}
                >
                  <div className="flex items-center space-x-2.5 min-w-0 flex-1">
                    <MessageSquare
                      size={14}
                      className={isActive ? 'text-sky-600' : 'text-slate-400 group-hover:text-slate-500'}
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
                        className="bg-white border border-sky-500 rounded px-1.5 py-0.5 text-xs text-slate-800 outline-none w-36 shadow-inner"
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
                          className="p-1 hover:text-emerald-600 text-slate-400"
                          title="保存"
                        >
                          <Check size={13} />
                        </button>
                        <button
                          onClick={cancelRename}
                          className="p-1 hover:text-rose-600 text-slate-400"
                          title="取消"
                        >
                          <X size={13} />
                        </button>
                      </>
                    ) : (
                      <>
                        <button
                          onClick={(e) => startRename(s, e)}
                          className="p-1 hover:text-sky-600 text-slate-400 transition-colors"
                          title="重命名"
                        >
                          <Edit2 size={12} />
                        </button>
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            onDeleteSession(s.id);
                          }}
                          className="p-1 hover:text-rose-600 text-slate-400 transition-colors"
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

        {/* 底部系统状态指标卡片 */}
        <div className="p-3 border-t border-slate-200/80 bg-slate-100/50">
          <div className="rounded-xl bg-white border border-slate-200/90 p-3 space-y-2.5 text-xs shadow-sm">
            <div className="flex items-center justify-between text-slate-500">
              <div className="flex items-center space-x-1.5">
                <Cpu size={13} className="text-sky-600" />
                <span className="text-[11px] font-medium">模型后端</span>
              </div>
              <span className="text-slate-800 font-mono text-[11px] truncate max-w-[120px]" title={systemStatus?.model || 'Claude'}>
                {systemStatus?.model || 'Anthropic'}
              </span>
            </div>

            <div>
              <div className="flex items-center justify-between text-[11px] text-slate-500 mb-1">
                <div className="flex items-center space-x-1.5">
                  <span>上下文</span>
                  <span className="font-mono text-slate-700 text-[10px]">
                    {sessionCtx > 0 ? `${formatTokenCount(sessionCtx)} / ${formatTokenCount(contextWindow)}` : `0 / ${formatTokenCount(contextWindow)}`}
                  </span>
                </div>
                <span className="font-mono text-slate-500 text-[10px]" title={`精确占用: ${contextPct.toFixed(3)}% (${sessionCtx.toLocaleString()} / ${contextWindow.toLocaleString()} tokens)`}>
                  {contextPctLabel}
                </span>
              </div>
              <div className="w-full h-1.5 bg-slate-100 rounded-full overflow-hidden border border-slate-200">
                <div
                  className={`h-full ${pctColor} transition-all duration-500`}
                  style={{ width: `${sessionCtx > 0 ? Math.min(Math.max(contextPct, 2), 100) : 0}%` }}
                />
              </div>
            </div>

            {/* 本会话消耗与项目累计双层展示 (方案 A) */}
            <div className="pt-1.5 border-t border-slate-100 space-y-1 text-[11px] text-slate-500">
              <div className="flex items-center justify-between">
                <div className="flex items-center space-x-1">
                  <Activity size={12} className="text-sky-600" />
                  <span className="font-medium text-slate-600">本会话消耗</span>
                </div>
                <span className="font-mono text-slate-800 font-medium" title="当前会话所有往返交互消耗的输入与输出 Token 总和">
                  {(systemStatus?.session_tokens || 0).toLocaleString()}
                </span>
              </div>

              <div className="flex items-center justify-between text-[10px] text-slate-400">
                <span title="当前工作区下所有会话的累计消耗总和">项目总计开销</span>
                <span className="font-mono text-slate-500">
                  {((systemStatus?.project_total_tokens ?? (systemStatus?.total_input_tokens || 0) + (systemStatus?.total_output_tokens || 0))).toLocaleString()}
                </span>
              </div>
            </div>

            <button
              onClick={onTriggerCompact}
              className="w-full mt-1 py-1.5 px-2 bg-slate-50 hover:bg-slate-100 border border-slate-200 rounded-lg text-[11px] text-slate-600 hover:text-slate-900 flex items-center justify-center space-x-1.5 transition-colors"
              title="手动归档并压缩历史上下文"
            >
              <Archive size={12} className="text-amber-600" />
              <span>压缩会话 (/compact)</span>
            </button>
          </div>
        </div>
      </div>
    </aside>
  );
};
