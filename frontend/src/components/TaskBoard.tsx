import React from 'react';
import { CheckCircle2, Clock, PlayCircle, Lock, User, RefreshCw } from 'lucide-react';
import { TaskItem } from '../types/rove';

interface TaskBoardProps {
  tasks: TaskItem[];
  onRefresh: () => void;
  isLoading: boolean;
}

export const TaskBoard: React.FC<TaskBoardProps> = ({ tasks, onRefresh, isLoading }) => {
  const pendingTasks = tasks.filter((t) => t.status === 'pending');
  const inProgressTasks = tasks.filter((t) => t.status === 'in_progress');
  const completedTasks = tasks.filter((t) => t.status === 'completed');

  const renderTaskCard = (task: TaskItem) => {
    const isBlocked = task.blockedBy && task.blockedBy.length > 0;

    return (
      <div
        key={task.id}
        className="p-3 rounded-lg border border-rove-border bg-rove-card/70 hover:border-rove-cyan/30 transition-all text-xs font-sans space-y-2"
      >
        <div className="flex items-start justify-between">
          <span className="font-mono text-[11px] text-rove-cyan font-semibold">
            #{task.id}
          </span>
          {task.status === 'completed' ? (
            <span className="flex items-center space-x-1 text-[10px] text-rove-green font-mono">
              <CheckCircle2 size={12} />
              <span>DONE</span>
            </span>
          ) : task.status === 'in_progress' ? (
            <span className="flex items-center space-x-1 text-[10px] text-rove-yellow font-mono">
              <PlayCircle size={12} />
              <span>RUNNING</span>
            </span>
          ) : (
            <span className="flex items-center space-x-1 text-[10px] text-rove-textDim font-mono">
              <Clock size={12} />
              <span>WAITING</span>
            </span>
          )}
        </div>

        <h4 className="font-medium text-rove-textBright leading-snug">{task.subject}</h4>
        {task.description && (
          <p className="text-[11px] text-rove-textDim line-clamp-2 leading-relaxed">
            {task.description}
          </p>
        )}

        <div className="flex flex-wrap items-center justify-between pt-1 border-t border-rove-border/40 text-[10px] font-mono text-rove-textDim">
          {task.owner ? (
            <div className="flex items-center space-x-1 text-cyan-300">
              <User size={11} />
              <span>{task.owner}</span>
            </div>
          ) : (
            <span>未认领 (Unassigned)</span>
          )}

          {isBlocked && (
            <div className="flex items-center space-x-1 text-rose-400">
              <Lock size={11} />
              <span>等待 #{task.blockedBy.join(', #')}</span>
            </div>
          )}
        </div>
      </div>
    );
  };

  return (
    <div className="flex flex-col h-full overflow-hidden text-xs">
      <div className="flex items-center justify-between p-3 border-b border-rove-border">
        <span className="font-mono text-[11px] text-rove-textDim uppercase tracking-wider">
          Task Board ({tasks.length})
        </span>
        <button
          onClick={onRefresh}
          className="p-1 hover:bg-rove-card rounded text-rove-textDim hover:text-rove-cyan transition-colors"
          title="刷新任务看板"
        >
          <RefreshCw size={13} className={isLoading ? 'animate-spin' : ''} />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-3 space-y-4">
        {/* In Progress 分组 */}
        <div>
          <div className="flex items-center space-x-1.5 mb-2 text-[11px] font-mono text-rove-yellow uppercase tracking-wider">
            <PlayCircle size={12} />
            <span>进行中 ({inProgressTasks.length})</span>
          </div>
          {inProgressTasks.length === 0 ? (
            <div className="p-2 rounded bg-black/20 border border-dashed border-rove-border text-center text-rove-textDim text-[11px]">
              无进行中任务
            </div>
          ) : (
            <div className="space-y-2">{inProgressTasks.map(renderTaskCard)}</div>
          )}
        </div>

        {/* Pending 分组 */}
        <div>
          <div className="flex items-center space-x-1.5 mb-2 text-[11px] font-mono text-rove-cyan uppercase tracking-wider">
            <Clock size={12} />
            <span>等待中 ({pendingTasks.length})</span>
          </div>
          {pendingTasks.length === 0 ? (
            <div className="p-2 rounded bg-black/20 border border-dashed border-rove-border text-center text-rove-textDim text-[11px]">
              无排队任务
            </div>
          ) : (
            <div className="space-y-2">{pendingTasks.map(renderTaskCard)}</div>
          )}
        </div>

        {/* Completed 分组 */}
        <div>
          <div className="flex items-center space-x-1.5 mb-2 text-[11px] font-mono text-rove-green uppercase tracking-wider">
            <CheckCircle2 size={12} />
            <span>已完成 ({completedTasks.length})</span>
          </div>
          {completedTasks.length === 0 ? (
            <div className="p-2 rounded bg-black/20 border border-dashed border-rove-border text-center text-rove-textDim text-[11px]">
              暂无完成任务
            </div>
          ) : (
            <div className="space-y-2">{completedTasks.map(renderTaskCard)}</div>
          )}
        </div>
      </div>
    </div>
  );
};
