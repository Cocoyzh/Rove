import React, { useState, useMemo, useEffect, useRef, useCallback } from 'react';
import {
  X, ChevronDown, ChevronRight, Search, Copy, Check, Brain, Wrench, User,
  MessageSquare, Loader2, ListTree,
} from 'lucide-react';
import { CallEvent, CallBlock, CallEventStatus } from '../types/rove';

interface CallInspectorProps {
  isOpen: boolean;
  onClose: () => void;
  sessionId: string | null;
  callEvents: CallEvent[];
  isRunning?: boolean;
}

// ==================== 展示工具函数 ====================

const formatTokens = (n: number) => n.toLocaleString('en-US');

const formatLatency = (ms: number) => (ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`);

const formatClock = (ts: number) =>
  new Date(ts * 1000).toLocaleTimeString('zh-CN', { hour12: false });

const STATUS_BADGES: Record<CallEventStatus, { label: string; cls: string }> = {
  running: { label: '进行中', cls: 'bg-sky-100 text-sky-700 border-sky-200' },
  ok: { label: '正常结束', cls: 'bg-emerald-100 text-emerald-700 border-emerald-200' },
  tool_use: { label: '工具调用', cls: 'bg-amber-100 text-amber-700 border-amber-200' },
  error: { label: '错误', cls: 'bg-rose-100 text-rose-700 border-rose-200' },
};

const AGENT_LEAD = 'lead';

// ==================== 调用条目 ====================

const CallRow: React.FC<{
  event: CallEvent;
  expanded: boolean;
  onToggle: () => void;
  sessionId: string | null;
}> = ({ event, expanded, onToggle, sessionId }) => {
  const badge = STATUS_BADGES[event.status] ?? STATUS_BADGES.ok;
  const isRunning = event.status === 'running';

  return (
    <div
      className="border-b border-slate-100 last:border-b-0"
      style={{ contentVisibility: 'auto', containIntrinsicSize: '0 44px' } as React.CSSProperties}
    >
      {/* 摘要行：序号 + 双徽章 + IN/OUT + 耗时 + 墙钟 */}
      <div
        onClick={onToggle}
        className="flex items-center gap-2 px-3 py-2 cursor-pointer hover:bg-slate-50 select-none transition-colors"
      >
        <span className="text-[11px] font-mono text-slate-400 w-8 flex-shrink-0 text-right">
          #{event.seq}
        </span>

        {isRunning && <Loader2 size={12} className="text-sky-600 animate-spin flex-shrink-0" />}
        {expanded ? (
          <ChevronDown size={13} className="text-slate-400 flex-shrink-0" />
        ) : (
          <ChevronRight size={13} className="text-slate-300 flex-shrink-0" />
        )}

        {/* 会话类型徽章 */}
        <span
          className={`px-1.5 py-0.5 rounded text-[10px] font-medium border flex-shrink-0 ${
            event.agent_name === AGENT_LEAD
              ? 'bg-sky-50 text-sky-700 border-sky-200'
              : 'bg-violet-50 text-violet-700 border-violet-200'
          }`}
        >
          {event.agent_name === AGENT_LEAD ? '主会话' : event.agent_name}
        </span>

        {/* 状态徽章 */}
        <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium border flex-shrink-0 ${badge.cls}`}>
          {badge.label}
        </span>

        {/* 缓存命中徽章（context-optimize 合入前的占位消费） */}
        {!!event.cache_read_tokens && event.cache_read_tokens > 0 && (
          <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-teal-50 text-teal-700 border border-teal-200 flex-shrink-0">
            命中 {formatTokens(event.cache_read_tokens)}
          </span>
        )}

        <span className="flex-1" />

        <span className="text-[10px] font-mono text-slate-400 flex-shrink-0 truncate">
          IN {formatTokens(event.input_tokens || 0)} · OUT {formatTokens(event.output_tokens || 0)}
          {' · '}{formatLatency(event.latency_ms || 0)}
          {' · '}{formatClock(event.started_at)}
        </span>
      </div>

      {expanded && <CallDetail event={event} sessionId={sessionId} />}
    </div>
  );
};

// ==================== 展开详情：输入 / 输出块 ====================

const BLOCK_META: Record<string, { label: string; io: 'in' | 'out' }> = {
  user_message: { label: '用户消息', io: 'in' },
  tool_result: { label: '工具结果', io: 'in' },
  thinking: { label: '思考过程', io: 'out' },
  text: { label: '助手消息', io: 'out' },
  tool_use: { label: '工具调用', io: 'out' },
};

const BLOCK_ICONS: Record<string, React.ReactNode> = {
  user_message: <User size={11} className="text-slate-400" />,
  thinking: <Brain size={11} className="text-violet-400" />,
  text: <MessageSquare size={11} className="text-slate-400" />,
  tool_use: <Wrench size={11} className="text-amber-500" />,
  tool_result: <Wrench size={11} className="text-amber-500" />,
};

/** 解析 full_ref（"session:seq:blockIndex"）为 [seq, blockIndex] */
const parseFullRef = (ref?: string | null): [number, number] | null => {
  if (!ref) return null;
  const m = /^(.*):(\d+):(\d+)$/.exec(ref);
  if (!m) return null;
  return [parseInt(m[2], 10), parseInt(m[3], 10)];
};

const CallBlockView: React.FC<{
  block: CallBlock;
  seq: number;
  sessionId: string | null;
}> = ({ block, seq, sessionId }) => {
  const meta = BLOCK_META[block.type] ?? { label: block.type, io: 'out' as const };
  const [open, setOpen] = useState(block.type !== 'thinking'); // 思考过程默认折叠
  const [full, setFull] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [copied, setCopied] = useState(false);

  // preview 以省略号收尾且全文更长 → 可按 full_ref 拉取全量
  const hasMore = !!(
    block.full_len &&
    block.preview.endsWith('…') &&
    block.full_len > block.preview.length
  );

  const loadFull = useCallback(async () => {
    if (!sessionId || loading) return;
    const ref = parseFullRef(block.full_ref);
    if (!ref) return;
    setLoading(true);
    try {
      const res = await fetch(
        `/api/traces/${encodeURIComponent(sessionId)}/blocks/${ref[0]}/${ref[1]}`
      );
      if (res.ok) {
        const data = await res.json();
        setFull(data.full ?? null);
      }
    } catch {
      /* 拉取失败保持预览 */
    } finally {
      setLoading(false);
    }
  }, [sessionId, block.full_ref, loading]);

  const displayText = full ?? block.preview;

  const handleCopy = (e: React.MouseEvent) => {
    e.stopPropagation();
    const text = full ?? block.preview;
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  const isErrorResult = block.type === 'tool_result' && block.is_error;

  return (
    <div className="rounded-lg border border-slate-200 bg-white overflow-hidden">
      {/* 块头：类型标签 + 工具 chip + 调用 ID + 展开/复制 */}
      <div
        onClick={() => setOpen((prev) => !prev)}
        className="flex items-center gap-1.5 px-2 py-1.5 hover:bg-slate-50 cursor-pointer select-none"
      >
        {BLOCK_ICONS[block.type]}
        <span className="text-[10px] font-medium text-slate-500">{meta.label}</span>

        {block.tool_name && (
          <span className="px-1.5 py-0.5 rounded bg-amber-50 border border-amber-200 text-amber-700 text-[10px] font-mono font-semibold">
            {block.tool_name}
          </span>
        )}
        {block.tool_id && (
          <span className="text-[9px] font-mono text-slate-300 truncate">{block.tool_id}</span>
        )}
        {isErrorResult && (
          <span className="px-1 py-0.5 rounded bg-rose-100 text-rose-600 text-[9px] font-medium">
            错误
          </span>
        )}

        <span className="flex-1" />
        {hasMore && (
          <button
            onClick={(e) => {
              e.stopPropagation();
              if (!full) loadFull();
              setOpen(true);
            }}
            className="text-[9px] text-sky-600 hover:text-sky-800 flex items-center gap-0.5"
            title={`全文 ${block.full_len} 字符`}
          >
            {loading ? <Loader2 size={9} className="animate-spin" /> : <Search size={9} />}
            全文
          </button>
        )}
        <button
          onClick={handleCopy}
          className="text-slate-300 hover:text-sky-600 transition-colors"
          title="复制"
        >
          {copied ? <Check size={10} className="text-emerald-600" /> : <Copy size={10} />}
        </button>
        {open ? (
          <ChevronDown size={11} className="text-slate-300" />
        ) : (
          <ChevronRight size={11} className="text-slate-300" />
        )}
      </div>

      {open && (
        <pre
          className={`px-2.5 py-2 text-[10px] font-mono whitespace-pre-wrap break-all max-h-64 overflow-y-auto border-t ${
            isErrorResult
              ? 'bg-rose-50 border-rose-100 text-rose-700'
              : block.type === 'thinking'
              ? 'bg-violet-50/40 border-violet-100 text-slate-600'
              : 'bg-slate-50/60 border-slate-100 text-slate-700'
          }`}
        >
          {displayText || '(空)'}
          {hasMore && !full && (
            <span className="text-slate-400"> …点击"全文"拉取完整内容</span>
          )}
        </pre>
      )}
    </div>
  );
};

const CallDetail: React.FC<{ event: CallEvent; sessionId: string | null }> = ({
  event,
  sessionId,
}) => {
  // llm_call：输入=用户消息/工具结果，输出=思考/文本/工具调用决策；
  // tool_exec：输入=工具调用参数，输出=执行结果
  const inputTypes = event.kind === 'llm_call' ? ['user_message', 'tool_result'] : ['tool_use'];
  const inputBlocks = event.blocks.filter((b) => inputTypes.includes(b.type));
  const outputBlocks = event.blocks.filter((b) => !inputTypes.includes(b.type));

  return (
    <div className="px-3 pb-3 pt-1 space-y-2 bg-slate-50/50 border-t border-slate-100">
      {/* 错误信息 */}
      {event.error && (
        <pre className="px-2.5 py-2 rounded-lg bg-rose-50 border border-rose-200 text-rose-700 text-[10px] font-mono whitespace-pre-wrap max-h-32 overflow-y-auto">
          {event.error}
        </pre>
      )}

      {inputBlocks.length > 0 && (
        <div className="space-y-1.5">
          <div className="text-[9px] font-semibold text-slate-400 uppercase tracking-wider">输入</div>
          {inputBlocks.map((b, i) => (
            <CallBlockView key={`in-${i}`} block={b} seq={event.seq} sessionId={sessionId} />
          ))}
        </div>
      )}

      {outputBlocks.length > 0 && (
        <div className="space-y-1.5">
          <div className="text-[9px] font-semibold text-slate-400 uppercase tracking-wider">输出</div>
          {outputBlocks.map((b, i) => (
            <CallBlockView key={`out-${i}`} block={b} seq={event.seq} sessionId={sessionId} />
          ))}
        </div>
      )}

      {event.model && (
        <div className="text-[9px] font-mono text-slate-400">model: {event.model}</div>
      )}
    </div>
  );
};

// ==================== 调用检查器抽屉 ====================

type StatusFilter = 'all' | 'ok' | 'tool_use' | 'error';

export const CallInspector: React.FC<CallInspectorProps> = ({
  isOpen,
  onClose,
  sessionId,
  callEvents,
}) => {
  const [expandedSeq, setExpandedSeq] = useState<number | null>(null);
  const [agentFilter, setAgentFilter] = useState<string>('all');
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');
  const [search, setSearch] = useState('');
  const listRef = useRef<HTMLDivElement>(null);
  const prevCountRef = useRef(0);

  const agents = useMemo(() => {
    const names = new Set<string>();
    callEvents.forEach((e) => names.add(e.agent_name));
    return Array.from(names).sort();
  }, [callEvents]);

  const filtered = useMemo(() => {
    const kw = search.trim().toLowerCase();
    return callEvents.filter((e) => {
      if (agentFilter !== 'all' && e.agent_name !== agentFilter) return false;
      if (statusFilter === 'ok' && !(e.kind === 'llm_call' && e.status === 'ok')) return false;
      if (statusFilter === 'tool_use' && e.status !== 'tool_use') return false;
      if (statusFilter === 'error' && e.status !== 'error') return false;
      if (kw) {
        const hit = e.blocks.some(
          (b) =>
            (b.tool_name || '').toLowerCase().includes(kw) ||
            (b.preview || '').toLowerCase().includes(kw)
        );
        if (!hit) return false;
      }
      return true;
    });
  }, [callEvents, agentFilter, statusFilter, search]);

  // 新事件到达时贴底滚动（实时观测体验）
  useEffect(() => {
    if (callEvents.length !== prevCountRef.current) {
      prevCountRef.current = callEvents.length;
      const el = listRef.current;
      if (el) el.scrollTop = el.scrollHeight;
    }
  }, [callEvents.length]);

  const totalIn = useMemo(
    () => callEvents.reduce((s, e) => s + (e.input_tokens || 0), 0),
    [callEvents]
  );
  const totalOut = useMemo(
    () => callEvents.reduce((s, e) => s + (e.output_tokens || 0), 0),
    [callEvents]
  );

  return (
    <aside
      className={`h-full bg-slate-50 border-l border-slate-200 flex flex-col select-none flex-shrink-0 transition-all duration-300 ease-in-out ${
        isOpen ? 'w-96 opacity-100' : 'w-0 opacity-0 pointer-events-none'
      } overflow-hidden`}
    >
      <div className="w-96 h-full flex flex-col">
        {/* 头部 */}
        <div className="flex items-center justify-between border-b border-slate-200 bg-white px-3 py-2.5 shadow-sm">
          <div className="flex items-center space-x-1.5 min-w-0">
            <ListTree size={14} className="text-sky-600" />
            <span className="text-xs font-semibold text-slate-800">调用检查器</span>
            <span className="px-1.5 py-0.5 rounded-full bg-slate-100 text-slate-500 text-[10px] font-mono">
              {callEvents.length}
            </span>
          </div>
          <div className="flex items-center space-x-2 text-[10px] font-mono text-slate-400">
            <span title="会话累计输入/输出 token">Σ {formatTokens(totalIn)} / {formatTokens(totalOut)}</span>
            <button
              onClick={onClose}
              className="p-1 text-slate-400 hover:text-slate-700 hover:bg-slate-100 rounded-md transition-colors"
              title="关闭面板"
            >
              <X size={15} />
            </button>
          </div>
        </div>

        {/* 筛选栏 */}
        <div className="flex items-center gap-1.5 px-3 py-2 bg-white border-b border-slate-100">
          <select
            value={agentFilter}
            onChange={(e) => setAgentFilter(e.target.value)}
            className="text-[10px] border border-slate-200 rounded-md px-1.5 py-1 bg-white text-slate-600 outline-none"
          >
            <option value="all">全部 Agent</option>
            {agents.map((a) => (
              <option key={a} value={a}>
                {a === AGENT_LEAD ? '主会话' : a}
              </option>
            ))}
          </select>
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value as StatusFilter)}
            className="text-[10px] border border-slate-200 rounded-md px-1.5 py-1 bg-white text-slate-600 outline-none"
          >
            <option value="all">全部状态</option>
            <option value="ok">正常结束</option>
            <option value="tool_use">工具调用</option>
            <option value="error">错误</option>
          </select>
          <div className="flex-1 flex items-center gap-1 border border-slate-200 rounded-md px-2 py-1 bg-white min-w-0">
            <Search size={10} className="text-slate-300 flex-shrink-0" />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="按工具名/内容筛选"
              className="w-full text-[10px] outline-none bg-transparent text-slate-600 placeholder-slate-300"
            />
          </div>
        </div>

        {/* 调用列表（content-visibility 虚拟渲染，长会话不卡） */}
        <div ref={listRef} className="flex-1 overflow-y-auto bg-white">
          {filtered.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full text-center px-6 py-12">
              <ListTree size={28} className="text-slate-200 mb-3" />
              <p className="text-xs text-slate-400 leading-relaxed">
                {callEvents.length === 0
                  ? '暂无调用记录\n发送消息后，每次 LLM 调用与工具执行都会在这里实时出现'
                  : '当前筛选条件下没有匹配的调用'}
              </p>
            </div>
          ) : (
            filtered.map((event) => (
              <CallRow
                key={event.seq}
                event={event}
                expanded={expandedSeq === event.seq}
                onToggle={() => setExpandedSeq((prev) => (prev === event.seq ? null : event.seq))}
                sessionId={sessionId}
              />
            ))
          )}
        </div>
      </div>
    </aside>
  );
};
