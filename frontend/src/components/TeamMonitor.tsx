import React from 'react';
import { Users, Bot, RefreshCw, Moon, PowerOff, Zap } from 'lucide-react';
import { TeamConfig, SubagentRun } from '../types/rove';

interface TeamMonitorProps {
  team: TeamConfig | null;
  subagents?: SubagentRun[];
  onRefresh: () => void;
  isLoading: boolean;
}

// SubAgent 运行态卡片（数据来自 WebSocket subagent_status 事件；
// 更细的逐工具可视化归 tool-call-visualize 分支）
const SubagentList: React.FC<{ subagents: SubagentRun[] }> = ({ subagents }) => (
  <div className="space-y-2.5">
    {subagents.map((sub) => (
      <div
        key={sub.task_id}
        className="p-3 rounded-xl border border-slate-200 bg-white shadow-sm space-y-2"
      >
        <div className="flex items-center justify-between">
          <div className="flex items-center space-x-2 min-w-0">
            <div className="w-6 h-6 rounded-lg bg-violet-50 border border-violet-100 flex items-center justify-center text-violet-600 flex-shrink-0">
              <Zap size={13} />
            </div>
            <span className="font-semibold text-slate-800 text-xs">{sub.task_id}</span>
          </div>

          {sub.status === 'running' ? (
            <span className="flex items-center space-x-1 px-2 py-0.5 rounded-full bg-violet-50 border border-violet-200 text-[10px] text-violet-700 font-medium">
              <span className="w-1.5 h-1.5 rounded-full bg-violet-500 animate-pulse" />
              <span>探索中{sub.turn ? ` ${sub.turn}/${sub.max_turns ?? '?'}` : ''}</span>
            </span>
          ) : sub.status === 'error' ? (
            <span className="px-2 py-0.5 rounded-full bg-red-50 border border-red-200 text-[10px] text-red-700 font-medium">
              异常终止
            </span>
          ) : (
            <span className="px-2 py-0.5 rounded-full bg-emerald-50 border border-emerald-200 text-[10px] text-emerald-700 font-medium">
              已回报
            </span>
          )}
        </div>

        {sub.objective && (
          <p className="text-[11px] text-slate-600 leading-relaxed line-clamp-2" title={sub.objective}>
            {sub.objective}
          </p>
        )}

        <div className="pt-1.5 border-t border-slate-100 flex items-center justify-between text-[11px]">
          <span className="text-slate-400 truncate pr-2">
            {sub.activity || (sub.status === 'running' ? '等待子代理首轮输出…' : '')}
          </span>
          {typeof sub.total_tokens === 'number' && sub.status !== 'running' && (
            <span className="text-slate-500 font-medium flex-shrink-0">
              {sub.total_tokens.toLocaleString()} tok
            </span>
          )}
        </div>
      </div>
    ))}
  </div>
);

export const TeamMonitor: React.FC<TeamMonitorProps> = ({ team, subagents, onRefresh, isLoading }) => {
  const members = team?.members || [];
  const runs = subagents || [];

  return (
    <div className="flex flex-col h-full overflow-hidden text-xs">
      <div className="flex items-center justify-between p-3 border-b border-slate-200 bg-slate-50">
        <div className="flex items-center space-x-1.5 text-[11px] font-medium text-slate-400 uppercase tracking-wider">
          <Users size={13} className="text-sky-600" />
          <span>团队: {team?.team_name || 'default'} ({members.length})</span>
        </div>
        <button
          onClick={onRefresh}
          className="p-1 hover:bg-slate-200/60 rounded text-slate-400 hover:text-slate-700 transition-colors"
          title="刷新队友状态"
        >
          <RefreshCw size={13} className={isLoading ? 'animate-spin' : ''} />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-3 space-y-2.5">
        {runs.length > 0 && (
          <div className="space-y-1.5">
            <div className="flex items-center space-x-1.5 text-[11px] font-medium text-slate-400 uppercase tracking-wider pt-1">
              <Zap size={12} className="text-violet-600" />
              <span>SubAgent ({runs.filter((s) => s.status === 'running').length} 运行中)</span>
            </div>
            <SubagentList subagents={runs} />
          </div>
        )}

        {members.length === 0 && runs.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 text-center text-slate-400 space-y-2">
            <div className="w-10 h-10 rounded-full bg-slate-100 flex items-center justify-center text-slate-400">
              <Bot size={20} />
            </div>
            <p className="text-xs font-medium text-slate-600">暂无后台活跃的 Teammate</p>
            <p className="text-[11px] text-slate-400 max-w-[220px] leading-relaxed">
              当复杂子任务分配时，Lead Agent 会自动派生后台并发协同线程
            </p>
          </div>
        ) : (
          members.map((member) => {
            const isWorking = member.status === 'working';
            const isIdle = member.status === 'idle';

            return (
              <div
                key={member.name}
                className="p-3 rounded-xl border border-slate-200 bg-white hover:border-slate-300 shadow-sm transition-all space-y-2"
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center space-x-2">
                    <div className="w-6 h-6 rounded-lg bg-sky-50 border border-sky-100 flex items-center justify-center text-sky-600">
                      <Bot size={13} />
                    </div>
                    <div>
                      <span className="font-semibold text-slate-800 text-xs">
                        {member.name}
                      </span>
                    </div>
                  </div>

                  {/* 状态徽标 */}
                  {isWorking ? (
                    <span className="flex items-center space-x-1 px-2 py-0.5 rounded-full bg-emerald-50 border border-emerald-200 text-[10px] text-emerald-700 font-medium">
                      <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
                      <span>运行中</span>
                    </span>
                  ) : isIdle ? (
                    <span className="flex items-center space-x-1 px-2 py-0.5 rounded-full bg-amber-50 border border-amber-200 text-[10px] text-amber-700 font-medium">
                      <Moon size={10} />
                      <span>待机空闲</span>
                    </span>
                  ) : (
                    <span className="flex items-center space-x-1 px-2 py-0.5 rounded-full bg-slate-100 border border-slate-200 text-[10px] text-slate-500 font-medium">
                      <PowerOff size={10} />
                      <span>已退出</span>
                    </span>
                  )}
                </div>

                <div className="pt-1.5 border-t border-slate-100 flex items-center justify-between text-[11px]">
                  <span className="text-slate-400">角色分配:</span>
                  <span className="text-slate-700 font-medium">{member.role || 'Teammate'}</span>
                </div>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
};
