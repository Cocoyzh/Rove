import React, { useState, useRef, useEffect, useMemo } from 'react';
import { Send, Loader2, Sparkles, Sidebar as SidebarIcon, PanelLeft, ShieldCheck, ListTree } from 'lucide-react';
import { ChatMessage, ToolStep, ApprovalRequest, ApprovalMode, APPROVAL_MODE_LABELS, SystemStatus } from '../types/rove';
import { MessageItem } from './MessageItem';
import { groupMessagesIntoTurns } from '../utils/turnGrouper';

interface ChatAreaProps {
  sessionTitle: string;
  hasActiveSession?: boolean;
  isConnected: boolean;
  messages: ChatMessage[];
  currentStreamingText: string;
  activeToolSteps: ToolStep[];
  pendingApproval: ApprovalRequest | null;
  isRunning: boolean;
  approvalMode: ApprovalMode;
  systemStatus: SystemStatus | null;
  onSendMessage: (query: string) => void;
  onSendApproval: (approvalId: string, decision: string) => void;
  onChangeApprovalMode: (mode: ApprovalMode) => void;
  isSidebarCollapsed: boolean;
  onToggleSidebar: () => void;
  isCollabOpen: boolean;
  onToggleCollab: () => void;
  isInspectorOpen: boolean;
  onToggleInspector: () => void;
  renderApprovalCard?: (approval: ApprovalRequest) => React.ReactNode;
}

export const ChatArea: React.FC<ChatAreaProps> = ({
  sessionTitle,
  hasActiveSession = true,
  isConnected,
  messages,
  currentStreamingText,
  activeToolSteps,
  pendingApproval,
  isRunning,
  approvalMode,
  systemStatus,
  onSendMessage,
  onSendApproval,
  onChangeApprovalMode,
  isSidebarCollapsed,
  onToggleSidebar,
  isCollabOpen,
  onToggleCollab,
  isInspectorOpen,
  onToggleInspector,
  renderApprovalCard,
}) => {
  const [input, setInput] = useState('');
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // 将消息流聚合为自然连贯的对话轮次
  const turns = useMemo(() => {
    return groupMessagesIntoTurns(messages, activeToolSteps, currentStreamingText);
  }, [messages, activeToolSteps, currentStreamingText]);

  useEffect(() => {
    if (scrollContainerRef.current) {
      scrollContainerRef.current.scrollTop = scrollContainerRef.current.scrollHeight;
    }
  }, [turns, activeToolSteps, pendingApproval]);

  const handleSend = () => {
    if (!input.trim() || isRunning) return;
    onSendMessage(input.trim());
    setInput('');
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const adjustTextareaHeight = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInput(e.target.value);
    e.target.style.height = 'auto';
    e.target.style.height = `${Math.min(e.target.scrollHeight, 180)}px`;
  };

  const quickPrompts = [
    '分析当前仓库的项目结构与核心模块',
    '检查并总结当前的待办与看板任务',
    '查看所有的后台 Teammates 协同线程',
  ];

  return (
    <div className="flex-1 flex flex-col h-full overflow-hidden bg-white relative">
      {/* 顶部会话导航栏 */}
      <header className="h-14 border-b border-slate-200/80 px-4 md:px-6 flex items-center justify-between bg-white/90 backdrop-blur select-none z-10">
        <div className="flex items-center space-x-3 min-w-0">
          {/* 当侧边栏收起时展示的平滑展开按钮 */}
          <button
            onClick={onToggleSidebar}
            className={`p-1.5 rounded-lg text-slate-500 hover:text-slate-800 hover:bg-slate-100 transition-colors ${
              isSidebarCollapsed ? 'opacity-100' : 'opacity-70'
            }`}
            title={isSidebarCollapsed ? '展开左侧边栏' : '收起左侧边栏'}
          >
            <PanelLeft size={18} />
          </button>

          <span className="font-medium text-sm text-slate-800 truncate max-w-sm">
            {hasActiveSession ? (sessionTitle || '新会话') : '新会话'}
          </span>

          <div className="flex items-center space-x-1.5 text-xs">
            <span
              className={`w-2 h-2 rounded-full ${
                isConnected
                  ? 'bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.4)]'
                  : hasActiveSession
                  ? 'bg-rose-500'
                  : 'bg-emerald-400/80'
              }`}
            />
            <span className="text-[11px] text-slate-400 font-medium">
              {isConnected ? '在线就绪' : hasActiveSession ? '连接断开' : '等待开启'}
            </span>
          </div>
        </div>

        {/* 右侧：上下文仪表 + 权限模式切换 + 面板切换 */}
        <div className="flex items-center space-x-2">
          {/* Token 燃烧仪表：逼近压缩水位变色预警（L1 20% / L4 40%） */}
          <ContextGauge systemStatus={systemStatus} />

          <div
            className="flex items-center space-x-1.5 border border-slate-200 rounded-lg px-2 py-1.5 text-xs text-slate-600"
            title="权限审批模式：严格审查逐条审批；工作区信任自动放行区内写入；完全访问仅保留硬拒绝底线"
          >
            <ShieldCheck size={14} className="text-slate-400" />
            <select
              value={approvalMode}
              onChange={(e) => onChangeApprovalMode(e.target.value as ApprovalMode)}
              className="bg-transparent outline-none cursor-pointer font-medium text-slate-700"
            >
              {(Object.keys(APPROVAL_MODE_LABELS) as ApprovalMode[]).map((mode) => (
                <option key={mode} value={mode}>
                  {APPROVAL_MODE_LABELS[mode]}
                </option>
              ))}
            </select>
          </div>

          <button
            onClick={onToggleInspector}
            className={`px-2.5 py-1.5 rounded-lg border text-xs font-medium flex items-center space-x-1.5 transition-all ${
              isInspectorOpen
                ? 'bg-sky-50 border-sky-200 text-sky-700 shadow-xs'
                : 'border-slate-200 hover:bg-slate-50 text-slate-600 hover:text-slate-900'
            }`}
            title="切换调用检查器"
          >
            <ListTree size={14} />
            <span className="hidden md:inline">调用检查器</span>
          </button>

          <button
            onClick={onToggleCollab}
            className={`px-2.5 py-1.5 rounded-lg border text-xs font-medium flex items-center space-x-1.5 transition-all ${
              isCollabOpen
                ? 'bg-sky-50 border-sky-200 text-sky-700 shadow-xs'
                : 'border-slate-200 hover:bg-slate-50 text-slate-600 hover:text-slate-900'
            }`}
            title="切换协同看板"
          >
            <SidebarIcon size={14} />
            <span className="hidden sm:inline">协同看板</span>
          </button>
        </div>
      </header>

      {/* 对话消息滚动区 */}
      <div
        ref={scrollContainerRef}
        className="flex-1 overflow-y-auto px-4 md:px-12 py-6 space-y-3 scroll-smooth"
      >
        {messages.length === 0 && !currentStreamingText && activeToolSteps.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full text-center space-y-6 max-w-lg mx-auto py-12">
            <img src="/logo.svg" alt="Rove" className="w-14 h-14 shadow-sm" />
            <div>
              <h2 className="text-xl font-semibold text-slate-800 tracking-tight">
                欢迎使用 Rove 智能体工作台
              </h2>
              <p className="text-xs text-slate-500 mt-2 leading-relaxed max-w-md">
                由 Lead Agent 负责交互规划与验证，后台 Teammate 自主并发抢单协作。输入任务即可开始。
              </p>
            </div>

            {/* 推荐引导卡片 */}
            <div className="w-full grid grid-cols-1 gap-2 pt-2">
              {quickPrompts.map((p, idx) => (
                <button
                  key={idx}
                  onClick={() => onSendMessage(p)}
                  className="p-3 text-left rounded-xl bg-slate-50 hover:bg-slate-100/80 border border-slate-200 text-xs text-slate-700 hover:text-sky-700 transition-all flex items-center justify-between group shadow-xs"
                >
                  <span>{p}</span>
                  <Sparkles size={13} className="text-slate-400 group-hover:text-sky-600 transition-colors" />
                </button>
              ))}
            </div>
          </div>
        )}

        {/* 渲染按轮次聚合的对话（包含工具卡片与 Markdown 回复） */}
        {turns.map((turn) => (
          <MessageItem key={turn.id} turn={turn} />
        ))}

        {/* 权限审批请求卡片 */}
        {pendingApproval && renderApprovalCard && renderApprovalCard(pendingApproval)}

        {/* 思考中状态 */}
        {isRunning && !currentStreamingText && activeToolSteps.length === 0 && !pendingApproval && (
          <div className="flex items-center space-x-2 text-xs text-sky-700 py-2.5 px-3.5 rounded-xl bg-sky-50 border border-sky-100 w-fit animate-pulse shadow-xs">
            <Loader2 size={13} className="animate-spin text-sky-600" />
            <span>智能体正在思考与调度步骤...</span>
          </div>
        )}
      </div>

      {/* 底部输入框 */}
      <div className="p-4 md:p-6 bg-gradient-to-t from-white via-white to-transparent">
        <div className="max-w-3xl mx-auto relative rounded-2xl border border-slate-200/90 bg-white focus-within:border-sky-400 focus-within:ring-4 focus-within:ring-sky-100/60 transition-all shadow-md">
          <textarea
            ref={textareaRef}
            value={input}
            onChange={adjustTextareaHeight}
            onKeyDown={handleKeyDown}
            placeholder={
              isConnected
                ? '给 Rove 发送消息... (Enter 发送, Shift+Enter 换行)'
                : hasActiveSession
                ? '正在连接服务中...'
                : '给 Rove 发送消息，将自动开启新会话... (Enter 发送)'
            }
            disabled={isRunning || (!isConnected && hasActiveSession)}
            rows={1}
            className="w-full bg-transparent px-4 pt-3.5 pb-12 text-sm text-slate-800 placeholder-slate-400 resize-none outline-none font-sans"
          />

          <div className="absolute bottom-2.5 right-3 flex items-center space-x-2.5">
            <span className="text-[11px] text-slate-400 hidden sm:inline font-sans">
              Enter 发送
            </span>
            <button
              onClick={handleSend}
              disabled={!input.trim() || isRunning || (!isConnected && hasActiveSession)}
              className={`p-2 rounded-xl transition-all ${
                input.trim() && !isRunning && (isConnected || !hasActiveSession)
                  ? 'bg-sky-600 text-white hover:bg-sky-500 shadow-sm'
                  : 'bg-slate-100 text-slate-300 cursor-not-allowed'
              }`}
              title="发送"
            >
              {isRunning ? <Loader2 size={15} className="animate-spin" /> : <Send size={15} />}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};

/**
 * Token 燃烧仪表：实时显示当前会话上下文占用与剩余百分比。
 * 压缩水位（compaction_layers.py）：L1 20% 触发主动压缩，L4 40% 触发应急压缩——
 * 逼近水位时仪表变色预警，把"即将被压缩"这个不可见事件可视化。
 */
const L1_WATERMARK = 20;
const L4_WATERMARK = 40;

const formatCompactTokens = (n: number) =>
  n >= 1000 ? `${(n / 1000).toFixed(1)}k` : `${n}`;

const ContextGauge: React.FC<{ systemStatus: SystemStatus | null }> = ({ systemStatus }) => {
  if (!systemStatus || !systemStatus.context_window) return null;
  const pct = systemStatus.context_used_pct || 0;
  const used = systemStatus.session_context_tokens || 0;
  const color =
    pct >= L4_WATERMARK
      ? 'bg-rose-500'
      : pct >= L1_WATERMARK
      ? 'bg-amber-500'
      : 'bg-emerald-500';
  const textColor =
    pct >= L4_WATERMARK
      ? 'text-rose-600'
      : pct >= L1_WATERMARK
      ? 'text-amber-600'
      : 'text-slate-500';

  return (
    <div
      className="hidden lg:flex items-center space-x-1.5 border border-slate-200 rounded-lg px-2 py-1.5"
      title={`上下文占用 ${used.toLocaleString('en-US')} / ${systemStatus.context_window.toLocaleString('en-US')} tokens（${pct}%）\n≥${L1_WATERMARK}% 主动压缩 · ≥${L4_WATERMARK}% 应急压缩`}
    >
      <span className={`text-[10px] font-mono font-medium ${textColor}`}>
        {formatCompactTokens(used)} · {pct}%
      </span>
      <div className="w-14 h-1.5 rounded-full bg-slate-100 overflow-hidden">
        <div
          className={`h-full rounded-full transition-all duration-500 ${color}`}
          style={{ width: `${Math.min(100, Math.max(pct, pct > 0 ? 2 : 0))}%` }}
        />
      </div>
    </div>
  );
};
