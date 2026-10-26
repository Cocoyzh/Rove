export type Role = 'user' | 'assistant' | 'tool';

export interface ToolCall {
  tool_id: string;
  tool_name: string;
  tool_args: Record<string, any>;
}

export interface ChatMessage {
  id?: string;
  role: Role;
  content?: string;
  tool_calls?: ToolCall[];
  tool_call_id?: string;
}

export interface ToolStep {
  tool_id: string;
  tool_name: string;
  tool_args: Record<string, any>;
  output?: string;
  is_error?: boolean;
  cost_ms?: number;
  status: 'running' | 'completed' | 'error';
}

export type TurnBlock =
  | { type: 'text'; content: string }
  | { type: 'tool'; step: ToolStep };

export interface ChatTurn {
  id: string;
  role: 'user' | 'assistant';
  blocks: TurnBlock[];
}

export interface ApprovalRequest {
  approval_id: string;
  tool_name: string;
  arguments: Record<string, any>;
  reason: string;
  suggested_prefix?: string;
  target_path?: string;
  available_choices?: string[];
}

// 全局三档权限审批模式（与后端 ApprovalMode 对齐）
export type ApprovalMode = 'strict' | 'workspace' | 'full_access';

export const APPROVAL_MODE_LABELS: Record<ApprovalMode, string> = {
  strict: '严格审查',
  workspace: '工作区信任',
  full_access: '完全访问',
};

export interface SessionSummary {
  id: string;
  title: string;
  created_at: number;
  updated_at: number;
  message_count: number;
  total_input_tokens?: number;
  total_output_tokens?: number;
  last_context_tokens?: number;
  session_tokens?: number;
}

export interface TaskItem {
  id: number;
  subject: string;
  description: string;
  status: 'pending' | 'in_progress' | 'completed';
  blockedBy: number[];
  owner: string;
}

export interface TeammateMember {
  name: string;
  role: string;
  status: 'working' | 'idle' | 'shutdown' | 'crashed';
}

export interface TeamConfig {
  team_name: string;
  members: TeammateMember[];
}

// 一次性 SubAgent 运行态（后端 subagent_status WebSocket 事件）
export interface SubagentRun {
  task_id: string;
  status: 'running' | 'completed' | 'error';
  objective?: string;
  turn?: number;
  max_turns?: number;
  activity?: string;
  total_tokens?: number;
}

export interface SystemStatus {
  model: string;
  context_window: number;
  session_id?: string | null;
  session_context_tokens?: number;
  context_used_pct: number;
  session_tokens?: number;
  project_total_tokens?: number;
  total_input_tokens?: number;
  total_output_tokens?: number;
  cache_read_tokens?: number;
  cache_creation_tokens?: number;
  cache_hit_rate?: number;
  call_count?: number;
}

// ==================== CallTrace 调用检查器 ====================

export type CallEventKind = 'llm_call' | 'tool_exec';
// running 仅存在于 WS 实时流（进行中），落盘/回放均为终态
export type CallEventStatus = 'running' | 'ok' | 'error' | 'tool_use';
export type CallBlockType = 'user_message' | 'thinking' | 'text' | 'tool_use' | 'tool_result';

export interface CallBlock {
  type: CallBlockType;
  tool_name?: string | null;
  tool_id?: string | null;
  preview: string;
  full_len?: number;
  /** 形如 "{session_id}:{seq}:{block_index}"，指向该块全文所在位置（输入型 tool_result 指向原 tool_exec 事件） */
  full_ref?: string | null;
  is_error?: boolean | null;
}

export interface RequestComposition {
  system_segments?: string[];
  memory_block?: boolean;
  message_count?: number;
  tool_count?: number;
}

export interface CallEvent {
  seq: number;
  session_id: string;
  agent_name: string;
  kind: CallEventKind;
  status: CallEventStatus;
  model?: string | null;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens?: number | null;
  latency_ms: number;
  started_at: number;
  error?: string | null;
  request_composition?: RequestComposition | null;
  blocks: CallBlock[];
}
