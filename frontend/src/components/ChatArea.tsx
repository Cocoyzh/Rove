import React, { useState, useRef, useEffect } from 'react';
import { Send, Terminal, Loader2, Sparkles, Sidebar as SidebarIcon, CheckCircle2 } from 'lucide-react';
import { ChatMessage, ToolStep, ApprovalRequest } from '../types/rove';
import { MessageItem } from './MessageItem';

interface ChatAreaProps {
  sessionTitle: string;
  isConnected: boolean;
  messages: ChatMessage[];
  currentStreamingText: string;
  activeToolSteps: ToolStep[];
  pendingApproval: ApprovalRequest | null;
  isRunning: boolean;
  onSendMessage: (query: string) => void;
  onSendApproval: (approvalId: string, decision: 'y' | 's' | 'N') => void;
  isCollabOpen: boolean;
  onToggleCollab: () => void;
  renderApprovalCard?: (approval: ApprovalRequest) => React.ReactNode;
}

export const ChatArea: React.FC<ChatAreaProps> = ({
  sessionTitle,
  isConnected,
  messages,
  currentStreamingText,
  activeToolSteps,
  pendingApproval,
  isRunning,
  onSendMessage,
  onSendApproval,
  isCollabOpen,
  onToggleCollab,
  renderApprovalCard,
}) => {
  const [input, setInput] = useState('');
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // 智能自动滚动到底部
  useEffect(() => {
    if (scrollContainerRef.current) {
      scrollContainerRef.current.scrollTop = scrollContainerRef.current.scrollHeight;
    }
  }, [messages, currentStreamingText, activeToolSteps, pendingApproval]);

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
    '查看当前任务看板状态并汇报',
    '检查项目结构并读取 README.md',
    '列出所有后台 Teammates 的运行状态',
  ];

  return (
    <div className="flex-1 flex flex-col h-full overflow-hidden bg-rove-bg relative">
      {/* 顶部会话栏 */}
      <header className="h-14 border-b border-rove-border px-6 flex items-center justify-between bg-rove-sidebar/30 backdrop-blur select-none z-10">
        <div className="flex items-center space-x-3 min-w-0">
          <span className="font-mono text-sm font-semibold text-rove-textBright truncate max-w-md">
            {sessionTitle || '新会话'}
          </span>
          <div className="flex items-center space-x-1.5 text-xs">
            <span
              className={`w-2 h-2 rounded-full ${
                isConnected ? 'bg-rove-green shadow-[0_0_8px_rgba(16,185,129,0.5)]' : 'bg-rove-red'
              }`}
            />
            <span className="font-mono text-[10px] text-rove-textDim">
              {isConnected ? 'LIVE' : 'DISCONNECTED'}
            </span>
          </div>
        </div>

        <button
          onClick={onToggleCollab}
          className={`p-2 rounded-lg border text-xs flex items-center space-x-1.5 transition-colors ${
            isCollabOpen
              ? 'bg-rove-card border-rove-cyan/40 text-rove-cyan'
              : 'border-rove-border hover:bg-rove-card text-rove-textDim hover:text-rove-text'
          }`}
          title="切换协同面板 (Task 看板 & Teammates)"
        >
          <SidebarIcon size={15} />
          <span className="font-mono text-xs hidden sm:inline">协同面板</span>
        </button>
      </header>

      {/* 对话消息区 */}
      <div
        ref={scrollContainerRef}
        className="flex-1 overflow-y-auto px-4 md:px-8 py-6 space-y-2 scroll-smooth"
      >
        {messages.length === 0 && !currentStreamingText && activeToolSteps.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full text-center space-y-6 max-w-xl mx-auto py-12">
            <div className="w-12 h-12 rounded-2xl bg-rove-card border border-rove-border flex items-center justify-center text-rove-cyan shadow-[0_0_20px_rgba(34,211,238,0.15)]">
              <Terminal size={24} />
            </div>
            <div>
              <h2 className="text-lg font-mono font-bold text-rove-textBright tracking-wide">
                Rove Multi-Agent Harness
              </h2>
              <p className="text-xs text-rove-textDim mt-2 leading-relaxed">
                面向高复杂度编码任务的多智能体协作平台。由 Lead Agent 编排规划，后台 Teammate 自主并发抢单执行。
              </p>
            </div>

            {/* 快捷引导卡片 */}
            <div className="w-full grid grid-cols-1 gap-2 pt-4">
              {quickPrompts.map((p, idx) => (
                <button
                  key={idx}
                  onClick={() => onSendMessage(p)}
                  className="p-3 text-left rounded-lg bg-rove-card/60 hover:bg-rove-card border border-rove-border hover:border-rove-cyan/40 text-xs text-rove-text hover:text-rove-cyan transition-all flex items-center justify-between group"
                >
                  <span className="font-mono">{p}</span>
                  <Sparkles size={13} className="text-rove-textDim group-hover:text-rove-cyan opacity-0 group-hover:opacity-100 transition-opacity" />
                </button>
              ))}
            </div>
          </div>
        )}

        {/* 历史消息渲染 */}
        {messages.map((msg, index) => (
          <MessageItem key={msg.id || index} message={msg} />
        ))}

        {/* 当前流式吐字输出 */}
        {currentStreamingText && (
          <MessageItem
            message={{
              role: 'assistant',
              content: currentStreamingText,
            }}
            associatedToolSteps={activeToolSteps}
          />
        )}

        {/* 仅工具执行中但尚未产生后续文本时的占位展示 */}
        {!currentStreamingText && activeToolSteps.length > 0 && (
          <MessageItem
            message={{
              role: 'assistant',
              content: '',
            }}
            associatedToolSteps={activeToolSteps}
          />
        )}

        {/* 权限审批请求卡片 */}
        {pendingApproval && renderApprovalCard && renderApprovalCard(pendingApproval)}

        {/* 思考中加载态 */}
        {isRunning && !currentStreamingText && activeToolSteps.length === 0 && !pendingApproval && (
          <div className="flex items-center space-x-2 text-xs text-rove-cyan font-mono py-3 px-4 rounded-lg bg-rove-card/40 border border-rove-border w-fit animate-pulse">
            <Loader2 size={13} className="animate-spin" />
            <span>Lead Agent 正在分析并规划步骤...</span>
          </div>
        )}
      </div>

      {/* 底部输入控制条 */}
      <div className="p-4 md:p-6 border-t border-rove-border bg-rove-sidebar/40 backdrop-blur">
        <div className="max-w-4xl mx-auto relative rounded-xl border border-rove-border bg-rove-card/90 focus-within:border-rove-cyan/60 transition-all shadow-lg">
          <textarea
            ref={textareaRef}
            value={input}
            onChange={adjustTextareaHeight}
            onKeyDown={handleKeyDown}
            placeholder={
              isConnected
                ? '输入指令或编码需求... (Enter 发送, Shift+Enter 换行)'
                : '正在连接服务...'
            }
            disabled={!isConnected}
            rows={1}
            className="w-full bg-transparent px-4 pt-3.5 pb-12 text-sm text-rove-textBright placeholder-rove-textDim resize-none outline-none font-sans"
          />

          <div className="absolute bottom-2.5 right-3 flex items-center space-x-2">
            <span className="text-[10px] font-mono text-rove-textDim hidden sm:inline">
              Shift+Enter 换行
            </span>
            <button
              onClick={handleSend}
              disabled={!input.trim() || isRunning || !isConnected}
              className={`p-2 rounded-lg transition-all ${
                input.trim() && !isRunning && isConnected
                  ? 'bg-rove-cyan text-black hover:bg-rove-cyanHover shadow-[0_0_12px_rgba(34,211,238,0.3)]'
                  : 'bg-rove-border text-rove-textDim cursor-not-allowed'
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
