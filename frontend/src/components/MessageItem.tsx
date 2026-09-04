import React, { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import { User, Copy, Check, Compass } from 'lucide-react';
import { ChatMessage, ToolStep } from '../types/rove';
import { ToolCard } from './ToolCard';

interface MessageItemProps {
  message: ChatMessage;
  associatedToolSteps?: ToolStep[];
}

export const MessageItem: React.FC<MessageItemProps> = ({ message, associatedToolSteps = [] }) => {
  const isUser = message.role === 'user';
  const isTool = message.role === 'tool';

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
        <code className="bg-slate-100 text-sky-800 border border-slate-200/80 px-1.5 py-0.5 rounded-md font-mono text-xs" {...props}>
          {children}
        </code>
      );
    }

    return (
      <div className="relative my-3 rounded-xl border border-slate-800/80 bg-slate-900 overflow-hidden font-mono text-xs shadow-sm">
        <div className="flex items-center justify-between px-3.5 py-1.5 bg-slate-800/80 border-b border-slate-700/60 text-slate-400 text-[11px] select-none">
          <span>{language || 'text'}</span>
          <button
            onClick={handleCopy}
            className="flex items-center space-x-1 text-slate-400 hover:text-slate-200 transition-colors"
          >
            {copied ? <Check size={12} className="text-emerald-400" /> : <Copy size={12} />}
            <span>{copied ? '已复制' : '复制'}</span>
          </button>
        </div>
        <pre className="p-3.5 overflow-x-auto text-slate-100 leading-relaxed">
          <code>{children}</code>
        </pre>
      </div>
    );
  };

  if (isTool) {
    return null;
  }

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
      <div className={`flex max-w-[88%] md:max-w-[80%] ${isUser ? 'flex-row-reverse' : 'flex-row'} items-start space-x-3`}>
        {/* 头像 */}
        <div
          className={`w-8 h-8 rounded-xl flex items-center justify-center flex-shrink-0 mt-0.5 select-none shadow-xs ${
            isUser
              ? 'bg-slate-100 text-slate-600 ml-3 border border-slate-200'
              : 'bg-sky-50 text-sky-600 mr-3 border border-sky-100'
          }`}
        >
          {isUser ? <User size={16} /> : <Compass size={17} />}
        </div>

        {/* 气泡内容 */}
        <div
          className={`flex flex-col min-w-0 ${
            isUser
              ? 'bg-slate-100 text-slate-800 rounded-2xl rounded-tr-sm px-4 py-3 text-sm leading-relaxed border border-slate-200/60 shadow-xs'
              : 'w-full text-slate-800 text-sm leading-relaxed'
          }`}
        >
          {/* 用户或助手标识 */}
          <div className="flex items-center space-x-2 mb-1.5 select-none text-[11px] text-slate-400 font-medium">
            <span>{isUser ? 'You' : 'Rove Lead'}</span>
          </div>

          {/* Markdown 渲染 */}
          {content && (
            <div className="prose prose-slate max-w-none prose-sm leading-relaxed break-words text-slate-700">
              <ReactMarkdown
                components={{
                  code: CodeBlock,
                  p: ({ children }) => <p className="mb-2.5 last:mb-0 leading-relaxed">{children}</p>,
                  ul: ({ children }) => <ul className="list-disc pl-5 my-2 space-y-1">{children}</ul>,
                  ol: ({ children }) => <ol className="list-decimal pl-5 my-2 space-y-1">{children}</ol>,
                  blockquote: ({ children }) => (
                    <blockquote className="border-l-2 border-sky-400 pl-3 my-2 text-slate-500 italic">
                      {children}
                    </blockquote>
                  ),
                }}
              >
                {content}
              </ReactMarkdown>
            </div>
          )}

          {/* 历史工具调用 */}
          {message.tool_calls && message.tool_calls.length > 0 && (
            <div className="mt-2.5 space-y-1">
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

          {/* 当前进行中的工具步骤 */}
          {associatedToolSteps.length > 0 && (
            <div className="mt-2.5 space-y-1">
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
