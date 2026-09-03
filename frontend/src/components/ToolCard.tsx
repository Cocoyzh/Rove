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

  // 格式化参数摘要展示
  const argsSummary = Object.entries(step.tool_args || {})
    .map(([k, v]) => `${k}=${JSON.stringify(v)}`)
    .join(', ');

  return (
    <div className="my-2 rounded-lg border border-rove-border bg-rove-card/80 overflow-hidden text-xs font-mono transition-all">
      {/* 头部摘要栏 */}
      <div
        onClick={() => setIsExpanded((prev) => !prev)}
        className="flex items-center justify-between px-3 py-2 cursor-pointer hover:bg-rove-border/40 select-none"
      >
        <div className="flex items-center space-x-2 min-w-0 flex-1">
          {/* 状态图标 */}
          {isRunning ? (
            <Loader2 size={14} className="text-rove-cyan animate-spin flex-shrink-0" />
          ) : isError ? (
            <AlertCircle size={14} className="text-rove-red flex-shrink-0" />
          ) : (
            <CheckCircle2 size={14} className="text-rove-green flex-shrink-0" />
          )}

          {/* 工具名 */}
          <div className="flex items-center space-x-1.5 flex-shrink-0">
            <Wrench size={12} className="text-rove-yellow" />
            <span className="font-semibold text-rove-cyan">{step.tool_name}</span>
          </div>

          {/* 参数预览 */}
          <span className="text-rove-textDim truncate max-w-[450px]">
            ({argsSummary})
          </span>
        </div>

        {/* 右侧耗时与展开箭头 */}
        <div className="flex items-center space-x-2 flex-shrink-0 ml-3 text-rove-textDim">
          {step.cost_ms !== undefined && (
            <span className="text-[10px] text-rove-textDim">{step.cost_ms}ms</span>
          )}
          {isExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </div>
      </div>

      {/* 展开的详情部分 */}
      {isExpanded && (
        <div className="border-t border-rove-border p-3 space-y-2.5 bg-rove-bg/60">
          {/* 参数详情 */}
          <div>
            <div className="text-[10px] text-rove-textDim uppercase tracking-wider mb-1">Arguments</div>
            <pre className="p-2 rounded bg-black/40 border border-rove-border/60 text-rove-text text-[11px] overflow-x-auto">
              {JSON.stringify(step.tool_args, null, 2)}
            </pre>
          </div>

          {/* 执行结果 */}
          {step.output && (
            <div>
              <div className="flex items-center justify-between text-[10px] text-rove-textDim uppercase tracking-wider mb-1">
                <span>Output</span>
                <button
                  onClick={handleCopy}
                  className="flex items-center space-x-1 hover:text-rove-cyan transition-colors"
                >
                  {copied ? <Check size={11} className="text-rove-green" /> : <Copy size={11} />}
                  <span>{copied ? 'Copied' : 'Copy'}</span>
                </button>
              </div>
              <pre
                className={`p-2.5 rounded bg-black/40 border text-[11px] max-h-60 overflow-y-auto whitespace-pre-wrap ${
                  isError ? 'border-rove-red/40 text-red-300' : 'border-rove-border/60 text-rove-text'
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
