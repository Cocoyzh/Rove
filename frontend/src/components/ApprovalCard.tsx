import React from 'react';
import { ShieldAlert, Check, ShieldCheck, XCircle, Terminal, FileCode, Wrench } from 'lucide-react';
import { ApprovalRequest } from '../types/rove';

interface ApprovalCardProps {
  approval: ApprovalRequest;
  onRespond: (approvalId: string, decision: string) => void;
}

export const ApprovalCard: React.FC<ApprovalCardProps> = ({ approval, onRespond }) => {
  const isBash = ['bash', 'run_background'].includes(approval.tool_name);
  const isFile = ['write_file', 'edit_file'].includes(approval.tool_name);
  const prefix = approval.suggested_prefix;
  const path = approval.target_path;

  return (
    <div className="my-4 rounded-2xl border border-amber-200 bg-amber-50/60 p-4 shadow-sm text-sm font-sans">
      {/* 头部警告与原因 */}
      <div className="flex items-start space-x-3 mb-3">
        <div className="w-8 h-8 rounded-xl bg-amber-100 border border-amber-200 flex items-center justify-center text-amber-700 flex-shrink-0 mt-0.5 shadow-sm">
          <ShieldAlert size={17} />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center space-x-2">
            <span className="font-semibold text-amber-900 text-xs tracking-tight">
              操作安全审批 (Permission Required)
            </span>
            <span className="text-[11px] font-mono px-2 py-0.5 rounded-md bg-white text-slate-700 border border-slate-200 shadow-xs">
              {approval.tool_name}
            </span>
          </div>
          <p className="text-xs text-amber-800/80 mt-1 leading-relaxed">
            {approval.reason || 'Agent 请求执行具有写入或系统交互影响的敏感工具。'}
          </p>
        </div>
      </div>

      {/* 参数预览 */}
      <div className="mb-3.5">
        <div className="text-[11px] font-medium text-slate-500 uppercase tracking-wider mb-1">
          执行参数 (Arguments)
        </div>
        <pre className="p-3 rounded-xl bg-white border border-slate-200 text-xs font-mono text-slate-700 overflow-x-auto max-h-48 whitespace-pre-wrap leading-relaxed shadow-xs">
          {JSON.stringify(approval.arguments, null, 2)}
        </pre>
      </div>

      {/* 决策按钮组 */}
      <div className="flex flex-wrap items-center justify-end gap-2 pt-2 border-t border-amber-200/60">
        <button
          onClick={() => onRespond(approval.approval_id, 'N')}
          className="px-3 py-1.5 rounded-lg border border-slate-200 hover:bg-rose-50 hover:border-rose-200 text-slate-600 hover:text-rose-600 text-xs font-medium flex items-center space-x-1.5 transition-colors"
        >
          <XCircle size={14} />
          <span>拒绝 (Deny)</span>
        </button>

        {/* Bash 前缀放行按钮 */}
        {isBash && prefix && (
          <button
            onClick={() => onRespond(approval.approval_id, 'c')}
            title={`本会话放行 '${prefix} *' 开头的所有命令`}
            className="px-3 py-1.5 rounded-lg border border-amber-300 bg-amber-100/80 hover:bg-amber-200/80 hover:border-amber-400 text-amber-900 text-xs font-medium flex items-center space-x-1.5 transition-all shadow-xs"
          >
            <Terminal size={14} className="text-amber-700" />
            <span>放行前缀: <code className="font-mono font-semibold">{prefix} *</code></span>
          </button>
        )}

        {/* 文件路径放行按钮 */}
        {isFile && path && (
          <button
            onClick={() => onRespond(approval.approval_id, 'p')}
            title={`本会话放行对文件 '${path}' 的所有写入与修改`}
            className="px-3 py-1.5 rounded-lg border border-amber-300 bg-amber-100/80 hover:bg-amber-200/80 hover:border-amber-400 text-amber-900 text-xs font-medium flex items-center space-x-1.5 transition-all shadow-xs"
          >
            <FileCode size={14} className="text-amber-700" />
            <span>放行该文件: <code className="font-mono font-semibold">{path.length > 25 ? '...' + path.slice(-22) : path}</code></span>
          </button>
        )}

        {/* 工具级全局放行按钮 (非 Bash) */}
        {!isBash && (
          <button
            onClick={() => onRespond(approval.approval_id, 't')}
            title={`本会话放行工具 '${approval.tool_name}' 的所有后续调用`}
            className="px-3 py-1.5 rounded-lg border border-slate-200 bg-white hover:bg-slate-50 hover:border-slate-300 text-slate-700 text-xs font-medium flex items-center space-x-1.5 transition-all shadow-xs"
          >
            <Wrench size={14} className="text-amber-600" />
            <span>放行整个工具</span>
          </button>
        )}

        {/* 完全相同参数放行 */}
        <button
          onClick={() => onRespond(approval.approval_id, 's')}
          title="仅当后续工具参数与本次完全一致时才免批"
          className="px-3 py-1.5 rounded-lg border border-slate-200 bg-white hover:bg-slate-50 hover:border-slate-300 text-slate-700 text-xs font-medium flex items-center space-x-1.5 transition-all shadow-xs"
        >
          <ShieldCheck size={14} className="text-sky-600" />
          <span>仅相同参数免问</span>
        </button>

        {/* 单次允许 */}
        <button
          onClick={() => onRespond(approval.approval_id, 'y')}
          className="px-3.5 py-1.5 rounded-lg bg-sky-600 hover:bg-sky-500 text-white font-medium text-xs flex items-center space-x-1.5 transition-all shadow-sm"
        >
          <Check size={14} />
          <span>允许单次执行</span>
        </button>
      </div>
    </div>
  );
};
