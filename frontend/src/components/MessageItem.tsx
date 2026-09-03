import React, { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import { Terminal, User, Copy, Check } from 'lucide-react';
import { ChatMessage, ToolStep } from '../types/rove';
import { ToolCard } from './ToolCard';

interface MessageItemProps {
  message: ChatMessage;
  associatedToolSteps?: ToolStep[];
}

export const MessageItem: React.FC<MessageItemProps> = ({ message, associatedToolSteps = [] }) => {
  const isUser = message.role === 'user';
  const isTool = message.role === 'tool';

  // 内部代码块组件，提供右上角一键复制
  const CodeBlock = ({ className, children, ...props }: any) => {
    const [copied, setCopied] = useState(false);
    const match = /language-(\w+)/.exec(className || '');
    const language = match ? match[1] : '';
    const codeString = String(children).replace(/\n$/, '');

    const handleCopy = () => {
      navigator.clipboard.writeText(codeString);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    };

    if (!match && !codeString.includes('\n')) {
      return (
        <code className="bg-black/50 border border-rove-border px-1.5 py-0.5 rounded text-rove-cyan font-mono text-xs" {...props}>
          {children}
        </code>
      );
    }

    return (
      <div className="relative my-3 rounded-lg border border-rove-border bg-black/60 overflow-hidden font-mono text-xs">
        <div className="flex items-center justify-between px-3 py-1.5 bg-rove-card/70 border-b border-rove-border text-rove-textDim text-[11px] select-none">
          <span>{language || 'text'}</span>
          <button
            onClick={handleCopy}
            className="flex items-center space-x-1 hover:text-rove-cyan transition-colors"
          >
            {copied ? <Check size={12} className="text-rove-green" /> : <Copy size={12} />}
            <span>{copied ? 'Copied' : 'Copy'}</span>
          </button>
        </div>
        <pre className="p-3 overflow-x-auto text-rove-textBright leading-relaxed">
          <code>{children}</code>
        </pre>
      </div>
    );
  };

  if (isTool) {
    // 单独的 tool role 消息通常在前端不需要重复独立大段渲染，
    // 因为它们已经聚合在 ToolCard 步骤条中，但在查看详细历史时如果需要，可以极简提示
    return null;
  }

  // 过滤内部系统注入的标记，避免展示杂音（如 <inbox>...</inbox>）
  const content = message.content || '';
  const isSystemNotice =
    content.startsWith('<inbox>') ||
    content.startsWith('<background-results>') ||
    content.startsWith('<reminder>');
  if (isSystemNotice) {
    return null;
  }

  return (
    <div className={`flex w-full my-4 ${isUser ? 'justify-end' : 'justify-start'}`}>
      <div className={`flex max-w-[88%] ${isUser ? 'flex-row-reverse' : 'flex-row'} items-start space-x-3`}>
        {/* 头像 */}
        <div
          className={`w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0 mt-0.5 select-none ${
            isUser
              ? 'bg-rove-border text-rove-cyan ml-3 border border-rove-cyan/30'
              : 'bg-rove-card text-rove-cyan mr-3 border border-rove-border shadow-[0_0_10px_rgba(34,211,238,0.1)]'
          }`}
        >
          {isUser ? <User size={16} /> : <Terminal size={16} />}
        </div>

        {/* 气泡内容 */}
        <div
          className={`flex flex-col min-w-0 ${
            isUser
              ? 'bg-rove-card border border-rove-border rounded-2xl rounded-tr-none px-4 py-3 text-rove-textBright text-sm shadow-sm'
              : 'w-full text-rove-text text-sm'
          }`}
        >
          {/* 用户名字或智能体标志 */}
          <div className="flex items-center space-x-2 mb-1.5 select-none text-[11px] font-mono text-rove-textDim">
            <span>{isUser ? 'You' : 'Rove Lead Agent'}</span>
          </div>

          {/* Markdown 渲染 */}
          {content && (
            <div className="prose prose-invert max-w-none prose-sm leading-relaxed break-words">
              <ReactMarkdown
                components={{
                  code: CodeBlock,
                  p: ({ children }) => <p className="mb-2 last:mb-0">{children}</p>,
                  ul: ({ children }) => <ul className="list-disc pl-5 my-2 space-y-1">{children}</ul>,
                  ol: ({ children }) => <ol className="list-decimal pl-5 my-2 space-y-1">{children}</ol>,
                  blockquote: ({ children }) => (
                    <blockquote className="border-l-2 border-rove-cyan/60 pl-3 my-2 text-rove-textDim italic">
                      {children}
                    </blockquote>
                  ),
                }}
              >
                {content}
              </ReactMarkdown>
            </div>
          )}

          {/* 关联的工具调用列表 */}
          {message.tool_calls && message.tool_calls.length > 0 && (
            <div className="mt-2">
              {message.tool_calls.map((tc) => (
                <ToolCard
                  key={tc.tool_id}
                  step={{
                    tool_id: tc.tool_id,
                    tool_name: tc.tool_name,
                    tool_args: tc.tool_args,
                    status: 'completed',
                  }}
                />
              ))}
            </div>
          )}

          {/* 当前正在进行的工具步骤 */}
          {associatedToolSteps.length > 0 && (
            <div className="mt-2 space-y-1">
              {associatedToolSteps.map((step) => (
                <ToolCard key={step.tool_id} step={step} />
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
