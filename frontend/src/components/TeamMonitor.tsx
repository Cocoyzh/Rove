import React from 'react';
import { Users, Bot, RefreshCw, Activity, Moon, PowerOff } from 'lucide-react';
import { TeamConfig } from '../types/rove';

interface TeamMonitorProps {
  team: TeamConfig | null;
  onRefresh: () => void;
  isLoading: boolean;
}

export const TeamMonitor: React.FC<TeamMonitorProps> = ({ team, onRefresh, isLoading }) => {
  const members = team?.members || [];

  return (
    <div className="flex flex-col h-full overflow-hidden text-xs">
      <div className="flex items-center justify-between p-3 border-b border-rove-border">
        <div className="flex items-center space-x-1.5 font-mono text-[11px] text-rove-textDim uppercase tracking-wider">
          <Users size={13} />
          <span>Team: {team?.team_name || 'default'} ({members.length})</span>
        </div>
        <button
          onClick={onRefresh}
          className="p-1 hover:bg-rove-card rounded text-rove-textDim hover:text-rove-cyan transition-colors"
          title="刷新队友状态"
        >
          <RefreshCw size={13} className={isLoading ? 'animate-spin' : ''} />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-3 space-y-2.5">
        {members.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 text-center text-rove-textDim space-y-2">
            <Bot size={24} className="text-rove-border" />
            <p className="text-xs">暂无后台活跃的 Teammate</p>
            <p className="text-[10px] text-rove-textDim/70">
              Lead Agent 可在需要时使用 spawn_teammate 派生自主协同线程
            </p>
          </div>
        ) : (
          members.map((member) => {
            const isWorking = member.status === 'working';
            const isIdle = member.status === 'idle';

            return (
              <div
                key={member.name}
                className="p-3 rounded-lg border border-rove-border bg-rove-card/70 hover:border-rove-border/80 transition-all space-y-2"
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center space-x-2">
                    <div className="w-6 h-6 rounded-md bg-black/40 border border-rove-border flex items-center justify-center text-rove-cyan">
                      <Bot size={14} />
                    </div>
                    <div>
                      <span className="font-mono font-semibold text-rove-textBright text-xs">
                        {member.name}
                      </span>
                    </div>
                  </div>

                  {/* 状态徽标 */}
                  {isWorking ? (
                    <span className="flex items-center space-x-1 px-2 py-0.5 rounded-full bg-emerald-950/60 border border-emerald-800/40 text-[10px] text-rove-green font-mono">
                      <span className="w-1.5 h-1.5 rounded-full bg-rove-green animate-pulse" />
                      <span>WORKING</span>
                    </span>
                  ) : isIdle ? (
                    <span className="flex items-center space-x-1 px-2 py-0.5 rounded-full bg-yellow-950/50 border border-yellow-800/40 text-[10px] text-rove-yellow font-mono">
                      <Moon size={10} />
                      <span>IDLE</span>
                    </span>
                  ) : (
                    <span className="flex items-center space-x-1 px-2 py-0.5 rounded-full bg-black/50 border border-rove-border text-[10px] text-rove-textDim font-mono">
                      <PowerOff size={10} />
                      <span>SHUTDOWN</span>
                    </span>
                  )}
                </div>

                <div className="pt-1 border-t border-rove-border/40 flex items-center justify-between text-[11px]">
                  <span className="text-rove-textDim">Role:</span>
                  <span className="text-rove-cyan font-mono">{member.role || 'Teammate'}</span>
                </div>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
};
