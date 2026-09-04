import React, { useState, useEffect, useCallback } from 'react';
import { Kanban, Users, X } from 'lucide-react';
import { TaskBoard } from './TaskBoard';
import { TeamMonitor } from './TeamMonitor';
import { TaskItem, TeamConfig } from '../types/rove';

interface CollabPanelProps {
  isOpen: boolean;
  onClose: () => void;
  sessionId?: string | null;
}

export const CollabPanel: React.FC<CollabPanelProps> = ({ isOpen, onClose, sessionId }) => {
  const [activeTab, setActiveTab] = useState<'tasks' | 'team'>('tasks');
  const [tasks, setTasks] = useState<TaskItem[]>([]);
  const [team, setTeam] = useState<TeamConfig | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const fetchCollabData = useCallback(async () => {
    setIsLoading(true);
    try {
      const tasksUrl = sessionId ? `/api/tasks?session_id=${encodeURIComponent(sessionId)}` : '/api/tasks';
      const teamUrl = sessionId ? `/api/team?session_id=${encodeURIComponent(sessionId)}` : '/api/team';
      const [tasksRes, teamRes] = await Promise.all([
        fetch(tasksUrl),
        fetch(teamUrl),
      ]);

      if (tasksRes.ok) {
        const tasksData = await tasksRes.json();
        setTasks(Array.isArray(tasksData) ? tasksData : []);
      }

      if (teamRes.ok) {
        const teamData = await teamRes.json();
        setTeam(teamData);
      }
    } catch (err) {
      console.error('Failed to fetch collab data:', err);
    } finally {
      setIsLoading(false);
    }
  }, [sessionId]);

  useEffect(() => {
    if (isOpen) {
      fetchCollabData();
      const timer = setInterval(fetchCollabData, 4000);
      return () => clearInterval(timer);
    }
  }, [isOpen, fetchCollabData, sessionId]);

  return (
    <aside
      className={`h-full bg-slate-50 border-l border-slate-200 flex flex-col select-none flex-shrink-0 transition-all duration-300 ease-in-out ${
        isOpen ? 'w-80 opacity-100' : 'w-0 opacity-0 pointer-events-none'
      } overflow-hidden`}
    >
      <div className="w-80 h-full flex flex-col">
        {/* 头部 Tab 切换栏 */}
        <div className="flex items-center justify-between border-b border-slate-200 bg-white px-3 py-2.5 shadow-sm">
          <div className="flex items-center space-x-1.5">
            <button
              onClick={() => setActiveTab('tasks')}
              className={`px-3 py-1.5 rounded-lg text-xs font-medium flex items-center space-x-1.5 transition-all ${
                activeTab === 'tasks'
                  ? 'bg-sky-50 text-sky-700 border border-sky-200 shadow-sm'
                  : 'text-slate-500 hover:text-slate-800 hover:bg-slate-100'
              }`}
            >
              <Kanban size={13} />
              <span>任务看板 ({tasks.length})</span>
            </button>

            <button
              onClick={() => setActiveTab('team')}
              className={`px-3 py-1.5 rounded-lg text-xs font-medium flex items-center space-x-1.5 transition-all ${
                activeTab === 'team'
                  ? 'bg-sky-50 text-sky-700 border border-sky-200 shadow-sm'
                  : 'text-slate-500 hover:text-slate-800 hover:bg-slate-100'
              }`}
            >
              <Users size={13} />
              <span>队友状态 ({team?.members?.length || 0})</span>
            </button>
          </div>

          <button
            onClick={onClose}
            className="p-1 text-slate-400 hover:text-slate-700 hover:bg-slate-100 rounded-md transition-colors"
            title="关闭面板"
          >
            <X size={15} />
          </button>
        </div>

        {/* 活跃 Tab 内容 */}
        <div className="flex-1 overflow-hidden bg-slate-50">
          {activeTab === 'tasks' ? (
            <TaskBoard tasks={tasks} onRefresh={fetchCollabData} isLoading={isLoading} />
          ) : (
            <TeamMonitor team={team} onRefresh={fetchCollabData} isLoading={isLoading} />
          )}
        </div>
      </div>
    </aside>
  );
};
