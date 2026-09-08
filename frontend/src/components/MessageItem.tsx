import React, { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { User, Copy, Check } from 'lucide-react';
import { ChatTurn } from '../types/rove';
import { ToolCard } from './ToolCard';

interface MessageItemProps {
  turn: ChatTurn;
}

export const MessageItem: React.FC<MessageItemProps> = ({ turn }) => {
  const isUser = turn.role === 'user';

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

  const markdownComponents = {
    code: CodeBlock,
    p: ({ children }: any) => <p className="mb-2.5 last:mb-0 leading-relaxed">{children}</p>,
    ul: ({ children }: any) => <ul className="list-disc pl-5 my-2 space-y-1">{children}</ul>,
    ol: ({ children }: any) => <ol className="list-decimal pl-5 my-2 space-y-1">{children}</ol>,
    blockquote: ({ children }: any) => (
      <blockquote className="border-l-2 border-sky-400 pl-3 my-2 text-slate-500 italic">
        {children}
      </blockquote>
    ),
    table: ({ children }: any) => (
      <div className="overflow-x-auto my-3 rounded-xl border border-slate-200 shadow-xs bg-white">
        <table className="min-w-full divide-y divide-slate-200 text-xs text-left text-slate-700">
          {children}
        </table>
      </div>
    ),
    thead: ({ children }: any) => (
      <thead className="bg-slate-50/90 font-medium text-slate-600 select-none border-b border-slate-200">
        {children}
      </thead>
    ),
    tbody: ({ children }: any) => (
      <tbody className="divide-y divide-slate-100 bg-white">
        {children}
      </tbody>
    ),
    tr: ({ children }: any) => (
      <tr className="hover:bg-slate-50/60 transition-colors">
        {children}
      </tr>
    ),
    th: ({ children }: any) => (
      <th className="px-3.5 py-2.5 font-semibold text-slate-700 border-r border-slate-200/60 last:border-r-0 text-left whitespace-nowrap">
        {children}
      </th>
    ),
    td: ({ children }: any) => (
      <td className="px-3.5 py-2.5 whitespace-normal leading-relaxed border-r border-slate-100 last:border-r-0 text-slate-600">
        {children}
      </td>
    ),
    del: ({ children }: any) => <del className="line-through text-slate-400">{children}</del>,
    a: ({ href, children }: any) => (
      <a
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        className="text-sky-600 hover:text-sky-700 underline font-medium"
      >
        {children}
      </a>
    ),
  };

  return (
    <div className={`flex w-full my-4 ${isUser ? 'justify-end' : 'justify-start'}`}>
      <div className={`flex max-w-[88%] md:max-w-[80%] ${isUser ? 'flex-row-reverse' : 'flex-row'} items-start space-x-3`}>
        {/* 头像 */}
        {isUser ? (
          <div className="w-8 h-8 rounded-xl flex items-center justify-center flex-shrink-0 mt-0.5 select-none shadow-xs bg-slate-100 text-slate-600 ml-3 border border-slate-200">
            <User size={16} />
          </div>
        ) : (
          <img
            src="/logo.svg"
            alt="Rove"
            className="w-8 h-8 rounded-xl flex-shrink-0 mt-0.5 mr-3 select-none shadow-xs"
          />
        )}

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

          {/* 轮次内容渲染（按自然时间流渲染工具调用与 Markdown 文本） */}
          <div className="space-y-2">
            {turn.blocks.map((block, idx) => {
              if (block.type === 'tool' && block.step) {
                return <ToolCard key={block.step.tool_id || `tool-${idx}`} step={block.step} />;
              }
              if (block.type === 'text' && block.content) {
                return (
                  <div key={`text-${idx}`} className="prose prose-slate max-w-none prose-sm leading-relaxed break-words text-slate-700">
                    <ReactMarkdown
                      remarkPlugins={[remarkGfm]}
                      components={markdownComponents}
                    >
                      {block.content}
                    </ReactMarkdown>
                  </div>
                );
              }
              return null;
            })}
          </div>
        </div>
      </div>
    </div>
  );
};
