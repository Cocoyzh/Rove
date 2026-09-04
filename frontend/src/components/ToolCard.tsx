import React, { useState } from 'react';
import { ChevronDown, ChevronRight, Wrench, CheckCircle2, AlertCircle, Loader2, Copy, Check } from 'lucide-react';
import { ToolStep } from '../types/rove';

interface ToolCardProps {
  step: ToolStep;
}

export const ToolCard: React.FC<ToolCardProps> = ({ step }) => {
  const [isExpanded, setIsExpanded] = useState(false);
  const [copied, setCopied] = useState(false);

  const handleCopy = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (step.output) {
      navigator.clipboard.writeText(step.output);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  const isRunning = step.status === 'running';
  const isError = step.status === 'error' || step.is_error;

  const argsSummary = Object.entries(step.tool_args || {})
    .map(([k, v]) => `${k}=${JSON.stringify(v)}`)
    .join(', ');

  return (
    <div className="my-2 rounded-xl border border-slate-200 bg-white overflow-hidden text-xs font-sans transition-all shadow-xs">
      {/* 头部摘要栏 */}
      <div
        onClick={() => setIsExpanded((prev) => !prev)}
        className="flex items-center justify-between px-3.5 py-2.5 cursor-pointer hover:bg-slate-50 select-none transition-colors"
      >
        <div className="flex items-center space-x-2 min-w-0 flex-1">
          {isRunning ? (
            <Loader2 size={14} className="text-sky-600 animate-spin flex-shrink-0" />
          ) : isError ? (
            <AlertCircle size={14} className="text-rose-500 flex-shrink-0" />
          ) : (
            <CheckCircle2 size={14} className="text-emerald-600 flex-shrink-0" />
          )}

          <div className="flex items-center space-x-1.5 flex-shrink-0">
            <Wrench size={12} className="text-amber-500" />
            <span className="font-semibold text-slate-800 font-mono">{step.tool_name}</span>
          </div>

          <span className="text-slate-400 font-mono text-[11px] truncate max-w-[450px]">
            ({argsSummary})
          </span>
        </div>

        <div className="flex items-center space-x-2 flex-shrink-0 ml-3 text-slate-400">
          {step.cost_ms !== undefined && (
            <span className="text-[10px] font-mono text-slate-400">{step.cost_ms}ms</span>
          )}
          {isExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </div>
      </div>

      {/* 展开的详情部分 */}
      {isExpanded && (
        <div className="border-t border-slate-100 p-3 space-y-2.5 bg-slate-50/50">
          <div>
            <div className="text-[10px] font-medium text-slate-400 uppercase tracking-wider mb-1">调用参数</div>
            <pre className="p-2.5 rounded-lg bg-white border border-slate-200 text-slate-700 text-[11px] font-mono overflow-x-auto shadow-xs">
              {JSON.stringify(step.tool_args, null, 2)}
            </pre>
          </div>

          {step.output && (
            <div>
              <div className="flex items-center justify-between text-[10px] font-medium text-slate-400 uppercase tracking-wider mb-1">
                <span>执行结果</span>
                <button
                  onClick={handleCopy}
                  className="flex items-center space-x-1 text-slate-400 hover:text-sky-600 transition-colors"
                >
                  {copied ? <Check size={11} className="text-emerald-600" /> : <Copy size={11} />}
                  <span>{copied ? '已复制' : '复制'}</span>
                </button>
              </div>
              <pre
                className={`p-2.5 rounded-lg border text-[11px] font-mono max-h-60 overflow-y-auto whitespace-pre-wrap ${
                  isError
                    ? 'bg-rose-50 border-rose-200 text-rose-700'
                    : 'bg-white border-slate-200 text-slate-700 shadow-xs'
                }`}
              >
                {step.output}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
};
