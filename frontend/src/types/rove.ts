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
  status: 'working' | 'idle' | 'shutdown';
}

export interface TeamConfig {
  team_name: string;
  members: TeammateMember[];
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
  call_count?: number;
}
