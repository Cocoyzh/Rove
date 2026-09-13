import React, { useState, useMemo } from 'react';
import { ChevronDown, ChevronRight, Wrench, CheckCircle2, AlertCircle, Loader2, Copy, Check, FileDiff } from 'lucide-react';
import { ToolStep } from '../types/rove';

interface ToolCardProps {
  step: ToolStep;
}

interface DiffLine {
  type: 'same' | 'del' | 'add';
  text: string;
}

/** 基于 LCS 的行级 diff；规模过大时退化为整段删/增，避免 O(n·m) 爆内存 */
function diffLines(oldStr: string, newStr: string): DiffLine[] {
  const a = oldStr.split('\n');
  const b = newStr.split('\n');
  if (a.length * b.length > 250_000) {
    return [
      ...a.map((t): DiffLine => ({ type: 'del', text: t })),
      ...b.map((t): DiffLine => ({ type: 'add', text: t })),
    ];
  }

  const dp: number[][] = Array.from({ length: a.length + 1 }, () =>
    new Array(b.length + 1).fill(0)
  );
  for (let i = a.length - 1; i >= 0; i--) {
    for (let j = b.length - 1; j >= 0; j--) {
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }

  const lines: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      lines.push({ type: 'same', text: a[i] });
      i++;
      j++;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      lines.push({ type: 'del', text: a[i++] });
    } else {
      lines.push({ type: 'add', text: b[j++] });
    }
  }
  while (i < a.length) lines.push({ type: 'del', text: a[i++] });
  while (j < b.length) lines.push({ type: 'add', text: b[j++] });
  return lines;
}

const DiffView: React.FC<{ lines: DiffLine[] }> = ({ lines }) => (
  <pre className="p-2.5 rounded-lg bg-white border border-slate-200 text-[11px] font-mono max-h-80 overflow-y-auto shadow-xs">
    {lines.map((l, idx) => (
      <div
        key={idx}
        className={
          l.type === 'del'
            ? 'bg-rose-50 text-rose-700 -mx-2.5 px-2.5'
            : l.type === 'add'
            ? 'bg-emerald-50 text-emerald-700 -mx-2.5 px-2.5'
            : 'text-slate-400'
        }
      >
        <span className="select-none opacity-60">{l.type === 'del' ? '- ' : l.type === 'add' ? '+ ' : '  '}</span>
        {l.text || ' '}
      </div>
    ))}
  </pre>
);

/** 长输出默认折叠阈值 */
const LONG_OUTPUT_THRESHOLD = 1500;

export const ToolCard: React.FC<ToolCardProps> = ({ step }) => {
  const [isExpanded, setIsExpanded] = useState(false);
  const [copied, setCopied] = useState(false);
  const [outputExpanded, setOutputExpanded] = useState(false);

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

  // 工具卡增强：edit_file 渲染行级 diff，write_file 渲染写入内容；目标文件 chip
  const args = step.tool_args || {};
  const targetPath = (args.path || args.file_path || args.filename || '') as string;
  const editDiff = useMemo(() => {
    if (step.tool_name === 'edit_file' && typeof args.old_string === 'string' && typeof args.new_string === 'string') {
      return diffLines(args.old_string, args.new_string);
    }
    if (step.tool_name === 'write_file' && typeof args.content === 'string') {
      return args.content.split('\n').map((t): DiffLine => ({ type: 'add', text: t }));
    }
    return null;
  }, [step.tool_name, args]);

  const isLongOutput = !!step.output && step.output.length > LONG_OUTPUT_THRESHOLD;
  const outputLines = step.output ? step.output.split('\n') : [];
  const outputCollapsed = isLongOutput && !outputExpanded;

  return (
    <div
      className={`my-2 rounded-xl border bg-white overflow-hidden text-xs font-sans transition-all shadow-xs ${
        isError ? 'border-rose-200' : 'border-slate-200'
      }`}
    >
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
          {isError && (
            <span
              className="px-1.5 py-0.5 rounded bg-rose-100 text-rose-600 text-[10px] font-medium"
              title="执行失败——可让 Agent 修复后重试该调用"
            >
              失败·可重试
            </span>
          )}
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

          {editDiff && (
            <div>
              <div className="flex items-center space-x-1 text-[10px] font-medium text-slate-400 uppercase tracking-wider mb-1">
                <FileDiff size={11} />
                <span>{step.tool_name === 'edit_file' ? '变更差异' : '写入内容'}</span>
                {targetPath && (
                  <span className="px-1.5 rounded bg-slate-100 border border-slate-200 text-slate-500 font-mono normal-case tracking-normal">
                    {targetPath}
                  </span>
                )}
              </div>
              <DiffView lines={editDiff} />
            </div>
          )}

          {step.output && (
            <div>
              <div className="flex items-center justify-between text-[10px] font-medium text-slate-400 uppercase tracking-wider mb-1">
                <span>执行结果{isLongOutput ? `（共 ${outputLines.length} 行）` : ''}</span>
                <div className="flex items-center space-x-2">
                  {isLongOutput && (
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        setOutputExpanded((prev) => !prev);
                      }}
                      className="text-slate-400 hover:text-sky-600 transition-colors normal-case tracking-normal"
                    >
                      {outputExpanded ? '收起' : `展开全部 ${outputLines.length} 行`}
                    </button>
                  )}
                  <button
                    onClick={handleCopy}
                    className="flex items-center space-x-1 text-slate-400 hover:text-sky-600 transition-colors"
                  >
                    {copied ? <Check size={11} className="text-emerald-600" /> : <Copy size={11} />}
                    <span>{copied ? '已复制' : '复制'}</span>
                  </button>
                </div>
              </div>
              <pre
                className={`p-2.5 rounded-lg border text-[11px] font-mono overflow-y-auto whitespace-pre-wrap ${
                  isError
                    ? 'bg-rose-50 border-rose-200 text-rose-700'
                    : 'bg-white border-slate-200 text-slate-700 shadow-xs'
                } ${outputCollapsed ? 'max-h-40' : 'max-h-96'}`}
              >
                {outputCollapsed ? outputLines.slice(0, 20).join('\n') : step.output}
                {outputCollapsed && (
                  <span className="text-slate-400">
                    {'\n'}… 点击"展开全部 {outputLines.length} 行"查看剩余内容
                  </span>
                )}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
};
