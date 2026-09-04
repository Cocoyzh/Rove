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
        className="p-3 rounded-xl border border-slate-200 bg-white hover:border-slate-300 shadow-sm transition-all text-xs font-sans space-y-2"
      >
        <div className="flex items-start justify-between">
          <span className="font-mono text-xs text-sky-600 font-semibold">
            #{task.id}
          </span>
          {task.status === 'completed' ? (
            <span className="flex items-center space-x-1 text-[10px] px-2 py-0.5 rounded-full bg-emerald-50 text-emerald-700 border border-emerald-200 font-medium">
              <CheckCircle2 size={11} />
              <span>已完成</span>
            </span>
          ) : task.status === 'in_progress' ? (
            <span className="flex items-center space-x-1 text-[10px] px-2 py-0.5 rounded-full bg-amber-50 text-amber-700 border border-amber-200 font-medium">
              <PlayCircle size={11} />
              <span>进行中</span>
            </span>
          ) : (
            <span className="flex items-center space-x-1 text-[10px] px-2 py-0.5 rounded-full bg-slate-100 text-slate-600 border border-slate-200 font-medium">
              <Clock size={11} />
              <span>待排队</span>
            </span>
          )}
        </div>

        <h4 className="font-medium text-slate-800 leading-snug">{task.subject}</h4>
        {task.description && (
          <p className="text-[11px] text-slate-500 line-clamp-2 leading-relaxed">
            {task.description}
          </p>
        )}

        <div className="flex flex-wrap items-center justify-between pt-1.5 border-t border-slate-100 text-[10px] text-slate-400">
          {task.owner ? (
            <div className="flex items-center space-x-1 text-slate-600">
              <User size={11} className="text-sky-600" />
              <span>{task.owner}</span>
            </div>
          ) : (
            <span>未分配</span>
          )}

          {isBlocked && (
            <div className="flex items-center space-x-1 text-rose-500 bg-rose-50 px-1.5 py-0.5 rounded">
              <Lock size={10} />
              <span>依赖 #{task.blockedBy.join(', #')}</span>
            </div>
          )}
        </div>
      </div>
    );
  };

  return (
    <div className="flex flex-col h-full overflow-hidden text-xs">
      <div className="flex items-center justify-between p-3 border-b border-slate-200 bg-slate-50">
        <span className="text-[11px] font-medium text-slate-400 uppercase tracking-wider">
          任务看板 ({tasks.length})
        </span>
        <button
          onClick={onRefresh}
          className="p-1 hover:bg-slate-200/60 rounded text-slate-400 hover:text-slate-700 transition-colors"
          title="刷新任务看板"
        >
          <RefreshCw size={13} className={isLoading ? 'animate-spin' : ''} />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-3 space-y-4">
        {/* In Progress 分组 */}
        <div>
          <div className="flex items-center space-x-1.5 mb-2 text-[11px] font-medium text-amber-700 uppercase tracking-wider">
            <PlayCircle size={12} />
            <span>进行中 ({inProgressTasks.length})</span>
          </div>
          {inProgressTasks.length === 0 ? (
            <div className="p-3 rounded-xl bg-white border border-dashed border-slate-200 text-center text-slate-400 text-xs">
              无进行中任务
            </div>
          ) : (
            <div className="space-y-2">{inProgressTasks.map(renderTaskCard)}</div>
          )}
        </div>

        {/* Pending 分组 */}
        <div>
          <div className="flex items-center space-x-1.5 mb-2 text-[11px] font-medium text-sky-700 uppercase tracking-wider">
            <Clock size={12} />
            <span>待办中 ({pendingTasks.length})</span>
          </div>
          {pendingTasks.length === 0 ? (
            <div className="p-3 rounded-xl bg-white border border-dashed border-slate-200 text-center text-slate-400 text-xs">
              无排队任务
            </div>
          ) : (
            <div className="space-y-2">{pendingTasks.map(renderTaskCard)}</div>
          )}
        </div>

        {/* Completed 分组 */}
        <div>
          <div className="flex items-center space-x-1.5 mb-2 text-[11px] font-medium text-emerald-700 uppercase tracking-wider">
            <CheckCircle2 size={12} />
            <span>已完成 ({completedTasks.length})</span>
          </div>
          {completedTasks.length === 0 ? (
            <div className="p-3 rounded-xl bg-white border border-dashed border-slate-200 text-center text-slate-400 text-xs">
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
