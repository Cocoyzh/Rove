import React, { useState, useEffect, useCallback } from 'react';
import { Kanban, Users, ChevronRight, X } from 'lucide-react';
import { TaskBoard } from './TaskBoard';
import { TeamMonitor } from './TeamMonitor';
import { TaskItem, TeamConfig } from '../types/rove';

interface CollabPanelProps {
  isOpen: boolean;
  onClose: () => void;
}

export const CollabPanel: React.FC<CollabPanelProps> = ({ isOpen, onClose }) => {
  const [activeTab, setActiveTab] = useState<'tasks' | 'team'>('tasks');
  const [tasks, setTasks] = useState<TaskItem[]>([]);
  const [team, setTeam] = useState<TeamConfig | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const fetchCollabData = useCallback(async () => {
    setIsLoading(true);
    try {
      const [tasksRes, teamRes] = await Promise.all([
        fetch('/api/tasks'),
        fetch('/api/team'),
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
  }, []);

  useEffect(() => {
    if (isOpen) {
      fetchCollabData();
      const timer = setInterval(fetchCollabData, 4000);
      return () => clearInterval(timer);
    }
  }, [isOpen, fetchCollabData]);

  if (!isOpen) return null;

  return (
    <aside className="w-80 h-full bg-rove-sidebar border-l border-rove-border flex flex-col transition-all duration-300 select-none z-20">
      {/* 头部 Tab 切换栏 */}
      <div className="flex items-center justify-between border-b border-rove-border bg-rove-card/50 px-2 pt-2">
        <div className="flex items-center space-x-1">
          <button
            onClick={() => setActiveTab('tasks')}
            className={`px-3 py-1.5 rounded-t-lg font-mono text-xs flex items-center space-x-1.5 transition-colors border-t border-x ${
              activeTab === 'tasks'
                ? 'bg-rove-sidebar text-rove-cyan border-rove-border font-semibold -mb-px'
                : 'border-transparent text-rove-textDim hover:text-rove-text'
            }`}
          >
            <Kanban size={13} />
            <span>Tasks ({tasks.length})</span>
          </button>

          <button
            onClick={() => setActiveTab('team')}
            className={`px-3 py-1.5 rounded-t-lg font-mono text-xs flex items-center space-x-1.5 transition-colors border-t border-x ${
              activeTab === 'team'
                ? 'bg-rove-sidebar text-rove-cyan border-rove-border font-semibold -mb-px'
                : 'border-transparent text-rove-textDim hover:text-rove-text'
            }`}
          >
            <Users size={13} />
            <span>Team ({team?.members?.length || 0})</span>
          </button>
        </div>

        <button
          onClick={onClose}
          className="p-1 mb-1.5 text-rove-textDim hover:text-rove-text hover:bg-rove-card rounded transition-colors"
          title="关闭协同面板"
        >
          <X size={15} />
        </button>
      </div>

      {/* 活跃 Tab 内容 */}
      <div className="flex-1 overflow-hidden bg-rove-sidebar">
        {activeTab === 'tasks' ? (
          <TaskBoard tasks={tasks} onRefresh={fetchCollabData} isLoading={isLoading} />
        ) : (
          <TeamMonitor team={team} onRefresh={fetchCollabData} isLoading={isLoading} />
        )}
      </div>
    </aside>
  );
};
