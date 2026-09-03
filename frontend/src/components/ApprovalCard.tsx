import React from 'react';
import { ShieldAlert, Check, ShieldCheck, XCircle } from 'lucide-react';
import { ApprovalRequest } from '../types/rove';

interface ApprovalCardProps {
  approval: ApprovalRequest;
  onRespond: (approvalId: string, decision: 'y' | 's' | 'N') => void;
}

export const ApprovalCard: React.FC<ApprovalCardProps> = ({ approval, onRespond }) => {
  return (
    <div className="my-4 rounded-xl border border-rove-yellow/50 bg-yellow-950/20 p-4 shadow-[0_0_20px_rgba(234,179,8,0.08)] backdrop-blur text-sm font-sans animate-in fade-in zoom-in-95 duration-200">
      {/* 头部警告与原因 */}
      <div className="flex items-start space-x-3 mb-3">
        <div className="w-8 h-8 rounded-lg bg-yellow-500/10 border border-yellow-500/30 flex items-center justify-center text-rove-yellow flex-shrink-0 mt-0.5">
          <ShieldAlert size={18} />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center space-x-2">
            <span className="font-mono font-bold text-rove-yellow text-xs tracking-wide uppercase">
              权限审批请求 (Permission Required)
            </span>
            <span className="text-[11px] font-mono px-1.5 py-0.2 rounded bg-black/40 text-rove-cyan border border-rove-border">
              {approval.tool_name}
            </span>
          </div>
          <p className="text-xs text-rove-textBright mt-1">
            {approval.reason || 'Agent 正在请求执行具有潜在风险的系统或文件操作。'}
          </p>
        </div>
      </div>

      {/* 参数预览 */}
      <div className="mb-4">
        <div className="text-[10px] font-mono text-rove-textDim uppercase tracking-wider mb-1">
          Target Arguments
        </div>
        <pre className="p-3 rounded-lg bg-black/60 border border-rove-border text-xs font-mono text-rove-textBright overflow-x-auto max-h-48 whitespace-pre-wrap leading-relaxed">
          {JSON.stringify(approval.arguments, null, 2)}
        </pre>
      </div>

      {/* 交互决策按钮 */}
      <div className="flex flex-wrap items-center justify-end gap-2.5 pt-1 border-t border-yellow-500/20">
        <button
          onClick={() => onRespond(approval.approval_id, 'N')}
          className="px-3 py-1.5 rounded-lg border border-rove-red/40 hover:bg-rove-red/10 text-rose-300 text-xs font-mono flex items-center space-x-1.5 transition-colors"
        >
          <XCircle size={14} />
          <span>[N] 拒绝 (Deny)</span>
        </button>

        <button
          onClick={() => onRespond(approval.approval_id, 's')}
          className="px-3 py-1.5 rounded-lg border border-rove-cyan/40 bg-rove-cyan/10 hover:bg-rove-cyan/20 text-rove-cyan text-xs font-mono flex items-center space-x-1.5 transition-colors shadow-sm"
        >
          <ShieldCheck size={14} />
          <span>[s] 本会话同操作免问 (Always for Session)</span>
        </button>

        <button
          onClick={() => onRespond(approval.approval_id, 'y')}
          className="px-3.5 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-black font-semibold text-xs font-mono flex items-center space-x-1.5 transition-colors shadow-[0_0_12px_rgba(16,185,129,0.3)]"
        >
          <Check size={14} />
          <span>[y] 允许单次 (Allow Once)</span>
        </button>
      </div>
    </div>
  );
};
