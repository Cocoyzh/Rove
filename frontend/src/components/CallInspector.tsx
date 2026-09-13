import React, { useState, useMemo, useEffect, useRef, useCallback } from 'react';
import {
  X, ChevronDown, ChevronRight, Search, Copy, Check, Brain, Wrench, User,
  MessageSquare, Loader2, ListTree, Activity, Play, Pause, SkipBack, SkipForward,
  Download, FileQuestion, Info,
} from 'lucide-react';
import { CallEvent, CallBlock, CallEventStatus, RequestComposition } from '../types/rove';

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

/** "这次调用为什么发生"：请求构成（system 各段、记忆块、消息/工具数量） */
const SEGMENT_LABELS: Record<string, string> = {
  identity: '身份',
  environment: '环境',
  tool_policy: '工具策略',
  memory_policy: '记忆策略',
  team_policy: '团队策略',
  skills: '技能',
  teammate_role: '队友角色',
};

const CompositionView: React.FC<{ composition: RequestComposition }> = ({ composition }) => (
  <div className="rounded-lg border border-sky-100 bg-sky-50/60 px-2.5 py-2 space-y-1">
    <div className="flex items-center gap-1 text-[9px] font-semibold text-sky-700 uppercase tracking-wider">
      <Info size={10} />
      请求构成（这次调用为什么发生）
    </div>
    {!!composition.system_segments?.length && (
      <div className="flex flex-wrap gap-1">
        {composition.system_segments.map((seg) => (
          <span
            key={seg}
            className="px-1.5 py-0.5 rounded bg-white border border-sky-200 text-sky-700 text-[9px] font-mono"
            title={`system 段：${seg}`}
          >
            {SEGMENT_LABELS[seg] ?? seg}
          </span>
        ))}
      </div>
    )}
    <div className="text-[10px] text-slate-500 font-mono">
      记忆块注入: {composition.memory_block ? '有' : '无'}
      {' · '}消息 {composition.message_count ?? '?'} 条
      {' · '}工具 {composition.tool_count ?? '?'} 个
    </div>
  </div>
);

const CallDetail: React.FC<{ event: CallEvent; sessionId: string | null }> = ({
  event,
  sessionId,
}) => {
  const [copiedJson, setCopiedJson] = useState(false);
  // llm_call：输入=用户消息/工具结果，输出=思考/文本/工具调用决策；
  // tool_exec：输入=工具调用参数，输出=执行结果
  const inputTypes = event.kind === 'llm_call' ? ['user_message', 'tool_result'] : ['tool_use'];
  const inputBlocks = event.blocks.filter((b) => inputTypes.includes(b.type));
  const outputBlocks = event.blocks.filter((b) => !inputTypes.includes(b.type));

  const copyEventJson = (e: React.MouseEvent) => {
    e.stopPropagation();
    navigator.clipboard.writeText(JSON.stringify(event, null, 2));
    setCopiedJson(true);
    setTimeout(() => setCopiedJson(false), 1500);
  };

  return (
    <div className="px-3 pb-3 pt-1 space-y-2 bg-slate-50/50 border-t border-slate-100">
      {/* 错误信息 */}
      {event.error && (
        <pre className="px-2.5 py-2 rounded-lg bg-rose-50 border border-rose-200 text-rose-700 text-[10px] font-mono whitespace-pre-wrap max-h-32 overflow-y-auto">
          {event.error}
        </pre>
      )}

      {/* 请求构成（仅 llm_call 且后端记录了 composition 时展示） */}
      {event.request_composition && (
        <CompositionView composition={event.request_composition} />
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
          <div className="flex items-center justify-between">
            <div className="text-[9px] font-semibold text-slate-400 uppercase tracking-wider">输出</div>
            <button
              onClick={copyEventJson}
              className="flex items-center gap-0.5 text-[9px] text-slate-400 hover:text-sky-600 transition-colors"
              title="复制本调用的完整 JSON"
            >
              {copiedJson ? <Check size={9} className="text-emerald-600" /> : <Copy size={9} />}
              {copiedJson ? '已复制' : '复制 JSON'}
            </button>
          </div>
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

// ==================== 运行时间线（瀑布图） ====================

const TIMELINE_BAR_COLORS: Record<CallEventStatus, string> = {
  running: 'bg-sky-400 animate-pulse',
  ok: 'bg-emerald-500',
  tool_use: 'bg-amber-500',
  error: 'bg-rose-500',
};

/** lead + teammates 的调用甘特时间线：并行 spawn 一目了然，点击跳转调用详情 */
const TimelineTab: React.FC<{ events: CallEvent[]; onJump: (seq: number) => void }> = ({
  events,
  onJump,
}) => {
  const lanes = useMemo(() => {
    const map = new Map<string, CallEvent[]>();
    events.forEach((e) => {
      if (!map.has(e.agent_name)) map.set(e.agent_name, []);
      map.get(e.agent_name)!.push(e);
    });
    return Array.from(map.entries());
  }, [events]);

  const { t0, span } = useMemo(() => {
    if (!events.length) return { t0: 0, span: 1 };
    let lo = Infinity;
    let hi = -Infinity;
    events.forEach((e) => {
      const dur = Math.max(e.latency_ms, 300) / 1000; // 进行中/瞬时调用至少给 300ms 可视宽度
      lo = Math.min(lo, e.started_at);
      hi = Math.max(hi, e.started_at + dur);
    });
    const pad = Math.max((hi - lo) * 0.02, 0.5);
    return { t0: lo - pad, span: Math.max(hi + pad - (lo - pad), 0.001) };
  }, [events]);

  if (!events.length) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-center px-6 py-12">
        <Activity size={28} className="text-slate-200 mb-3" />
        <p className="text-xs text-slate-400 leading-relaxed">暂无调用可绘制时间线</p>
      </div>
    );
  }

  return (
    <div className="p-3 space-y-3 overflow-y-auto h-full">
      {lanes.map(([agent, evts]) => (
        <div key={agent}>
          <div className="flex items-center gap-1.5">
            <span
              className={`px-1.5 py-0.5 rounded text-[10px] font-medium border ${
                agent === AGENT_LEAD
                  ? 'bg-sky-50 text-sky-700 border-sky-200'
                  : 'bg-violet-50 text-violet-700 border-violet-200'
              }`}
            >
              {agent === AGENT_LEAD ? '主会话' : agent}
            </span>
            <span className="text-[10px] font-mono text-slate-400">{evts.length} 次调用</span>
          </div>
          <div className="relative h-6 bg-slate-100 rounded-md mt-1 overflow-hidden">
            {evts.map((e) => {
              const dur = Math.max(e.latency_ms, 300) / 1000;
              const left = ((e.started_at - t0) / span) * 100;
              const width = Math.max((dur / span) * 100, 1);
              const badge = STATUS_BADGES[e.status] ?? STATUS_BADGES.ok;
              return (
                <button
                  key={e.seq}
                  onClick={() => onJump(e.seq)}
                  style={{ left: `${left}%`, width: `${width}%` }}
                  className={`absolute top-1 h-4 rounded-sm hover:opacity-80 transition-opacity ${TIMELINE_BAR_COLORS[e.status] ?? 'bg-slate-400'}`}
                  title={`#${e.seq} ${badge.label} · ${formatLatency(e.latency_ms)} · IN ${formatTokens(e.input_tokens)} / OUT ${formatTokens(e.output_tokens)}\n点击查看调用详情`}
                />
              );
            })}
          </div>
        </div>
      ))}
      <div className="flex justify-between text-[9px] font-mono text-slate-300 pt-1 border-t border-slate-100">
        <span>{formatClock(events.reduce((m, e) => Math.min(m, e.started_at), Infinity))}</span>
        <span>
          {formatClock(
            events.reduce((m, e) => Math.max(m, e.started_at + Math.max(e.latency_ms, 300) / 1000), 0)
          )}
        </span>
      </div>
    </div>
  );
};

// ==================== 导出会话 Markdown 复盘 ====================

const buildMarkdown = (events: CallEvent[], sessionId: string | null): string => {
  const lines: string[] = [];
  const llmCount = events.filter((e) => e.kind === 'llm_call').length;
  const toolCount = events.filter((e) => e.kind === 'tool_exec').length;
  const totalIn = events.reduce((s, e) => s + (e.input_tokens || 0), 0);
  const totalOut = events.reduce((s, e) => s + (e.output_tokens || 0), 0);

  lines.push('# Rove 会话复盘', '');
  lines.push(`- 会话 ID: ${sessionId ?? '-'}`);
  lines.push(`- 导出时间: ${new Date().toLocaleString('zh-CN')}`);
  lines.push(`- 调用: ${events.length} 次（LLM ${llmCount} · 工具 ${toolCount}）`);
  lines.push(`- Token: IN ${formatTokens(totalIn)} · OUT ${formatTokens(totalOut)}`, '');

  for (const e of events) {
    const badge = STATUS_BADGES[e.status]?.label ?? e.status;
    lines.push(`## #${e.seq} ${e.agent_name === AGENT_LEAD ? '主会话' : e.agent_name} — ${badge}`, '');
    lines.push(
      `- 时间: ${formatClock(e.started_at)} · 耗时: ${formatLatency(e.latency_ms)} · IN ${formatTokens(e.input_tokens)} / OUT ${formatTokens(e.output_tokens)}${e.cache_read_tokens ? ` · 缓存命中 ${formatTokens(e.cache_read_tokens)}` : ''}`
    );
    const comp = e.request_composition;
    if (comp) {
      lines.push(
        `- 请求构成: ${(comp.system_segments ?? []).join(' + ') || '-'} · 记忆块 ${comp.memory_block ? '有' : '无'} · 消息 ${comp.message_count ?? '?'} 条 · 工具 ${comp.tool_count ?? '?'} 个`
      );
    }
    if (e.error) {
      lines.push(`- 错误: \`${e.error}\``);
    }
    lines.push('');
    for (const b of e.blocks) {
      const label = BLOCK_META[b.type]?.label ?? b.type;
      lines.push(`### ${label}${b.tool_name ? ` \`${b.tool_name}\`` : ''}`, '');
      lines.push('```');
      lines.push(b.preview);
      lines.push('```', '');
    }
  }
  return lines.join('\n');
};

// ==================== 调用检查器抽屉 ====================

type StatusFilter = 'all' | 'ok' | 'tool_use' | 'error';
type InspectorTab = 'list' | 'timeline';

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
  const [activeTab, setActiveTab] = useState<InspectorTab>('list');
  // 回放模式：从 .rove/traces/ 加载的历史调用可逐步回放（复盘失败 run）
  const [replayMode, setReplayMode] = useState(false);
  const [replayCursor, setReplayCursor] = useState(0); // 当前播放到的调用数（0..N）
  const [isPlaying, setIsPlaying] = useState(false);
  const listRef = useRef<HTMLDivElement>(null);
  const prevCountRef = useRef(0);

  const sorted = useMemo(() => [...callEvents].sort((a, b) => a.seq - b.seq), [callEvents]);

  const agents = useMemo(() => {
    const names = new Set<string>();
    callEvents.forEach((e) => names.add(e.agent_name));
    return Array.from(names).sort();
  }, [callEvents]);

  const filtered = useMemo(() => {
    const kw = search.trim().toLowerCase();
    const base = sorted.filter((e) => {
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
    return replayMode ? base.slice(0, replayCursor) : base;
  }, [sorted, agentFilter, statusFilter, search, replayMode, replayCursor]);

  // 回放播放器：每 600ms 前进一步
  useEffect(() => {
    if (!isPlaying || !replayMode) return;
    const timer = setInterval(() => {
      setReplayCursor((prev) => {
        if (prev >= filteredFullCountRef.current) {
          setIsPlaying(false);
          return prev;
        }
        return prev + 1;
      });
    }, 600);
    return () => clearInterval(timer);
  }, [isPlaying, replayMode]);

  // filteredFullCountRef：不受 cursor 截断的过滤后总数
  const filteredFullCount = useMemo(() => {
    const kw = search.trim().toLowerCase();
    return sorted.filter((e) => {
      if (agentFilter !== 'all' && e.agent_name !== agentFilter) return false;
      if (statusFilter === 'ok' && !(e.kind === 'llm_call' && e.status === 'ok')) return false;
      if (statusFilter === 'tool_use' && e.status !== 'tool_use') return false;
      if (statusFilter === 'error' && e.status !== 'error') return false;
      if (kw) {
        return e.blocks.some(
          (b) =>
            (b.tool_name || '').toLowerCase().includes(kw) ||
            (b.preview || '').toLowerCase().includes(kw)
        );
      }
      return true;
    }).length;
  }, [sorted, agentFilter, statusFilter, search]);
  const filteredFullCountRef = useRef(filteredFullCount);
  filteredFullCountRef.current = filteredFullCount;

  // 新事件到达时贴底滚动（实时观测体验）
  useEffect(() => {
    if (callEvents.length !== prevCountRef.current) {
      prevCountRef.current = callEvents.length;
      if (listRef.current && !replayMode) {
        listRef.current.scrollTop = listRef.current.scrollHeight;
      }
    }
  }, [callEvents.length, replayMode]);

  const toggleReplay = () => {
    if (!replayMode) {
      setReplayMode(true);
      setReplayCursor(0);
      setIsPlaying(false);
    } else {
      setReplayMode(false);
      setIsPlaying(false);
    }
  };

  // 时间线条点击 → 跳到列表并展开对应调用
  const jumpToCall = (seq: number) => {
    setActiveTab('list');
    if (replayMode && seq > replayCursor) {
      setReplayCursor(filteredFullCountRef.current);
    }
    setExpandedSeq(seq);
  };

  const exportMarkdown = () => {
    const md = buildMarkdown(filtered, sessionId);
    const blob = new Blob([md], { type: 'text/markdown;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `rove-trace-${sessionId ?? 'session'}.md`;
    a.click();
    URL.revokeObjectURL(url);
  };

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
              onClick={exportMarkdown}
              className="p-1 text-slate-400 hover:text-sky-600 hover:bg-slate-100 rounded-md transition-colors"
              title="导出会话 Markdown 复盘"
            >
              <Download size={14} />
            </button>
            <button
              onClick={onClose}
              className="p-1 text-slate-400 hover:text-slate-700 hover:bg-slate-100 rounded-md transition-colors"
              title="关闭面板"
            >
              <X size={15} />
            </button>
          </div>
        </div>

        {/* Tab 切换：调用列表 / 运行时间线 */}
        <div className="flex items-center justify-between border-b border-slate-100 bg-white px-3 py-1.5">
          <div className="flex items-center space-x-1">
            <button
              onClick={() => setActiveTab('list')}
              className={`px-2.5 py-1 rounded-lg text-[11px] font-medium flex items-center space-x-1 transition-all ${
                activeTab === 'list'
                  ? 'bg-sky-50 text-sky-700 border border-sky-200'
                  : 'text-slate-500 hover:text-slate-800 hover:bg-slate-100 border border-transparent'
              }`}
            >
              <ListTree size={11} />
              <span>调用列表</span>
            </button>
            <button
              onClick={() => setActiveTab('timeline')}
              className={`px-2.5 py-1 rounded-lg text-[11px] font-medium flex items-center space-x-1 transition-all ${
                activeTab === 'timeline'
                  ? 'bg-sky-50 text-sky-700 border border-sky-200'
                  : 'text-slate-500 hover:text-slate-800 hover:bg-slate-100 border border-transparent'
              }`}
            >
              <Activity size={11} />
              <span>运行时间线</span>
            </button>
          </div>
          <button
            onClick={toggleReplay}
            className={`px-2 py-1 rounded-lg text-[11px] font-medium flex items-center space-x-1 transition-all ${
              replayMode
                ? 'bg-amber-50 text-amber-700 border border-amber-200'
                : 'text-slate-500 hover:text-slate-800 hover:bg-slate-100 border border-transparent'
            }`}
            title="回放模式：逐步重放历史调用（复盘失败 run）"
          >
            <SkipForward size={11} />
            <span>回放</span>
          </button>
        </div>

        {/* 回放 scrubber */}
        {replayMode && (
          <div className="flex items-center gap-1.5 px-3 py-2 bg-amber-50/60 border-b border-amber-100">
            <button
              onClick={() => setReplayCursor((p) => Math.max(0, p - 1))}
              className="p-0.5 text-slate-500 hover:text-slate-800"
              title="上一步"
            >
              <SkipBack size={12} />
            </button>
            <button
              onClick={() => {
                if (replayCursor >= filteredFullCount) setReplayCursor(0);
                setIsPlaying((p) => !p);
              }}
              className="p-0.5 text-slate-500 hover:text-slate-800"
              title={isPlaying ? '暂停' : '播放'}
            >
              {isPlaying ? <Pause size={12} /> : <Play size={12} />}
            </button>
            <button
              onClick={() => setReplayCursor((p) => Math.min(filteredFullCount, p + 1))}
              className="p-0.5 text-slate-500 hover:text-slate-800"
              title="下一步"
            >
              <SkipForward size={12} />
            </button>
            <input
              type="range"
              min={0}
              max={filteredFullCount}
              value={Math.min(replayCursor, filteredFullCount)}
              onChange={(e) => {
                setIsPlaying(false);
                setReplayCursor(parseInt(e.target.value, 10));
              }}
              className="flex-1 h-1 accent-amber-500"
            />
            <span className="text-[10px] font-mono text-slate-500 w-10 text-right">
              {Math.min(replayCursor, filteredFullCount)}/{filteredFullCount}
            </span>
          </div>
        )}

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

        {/* 内容区 */}
        <div ref={listRef} className="flex-1 overflow-y-auto bg-white">
          {activeTab === 'timeline' ? (
            <TimelineTab events={filtered} onJump={jumpToCall} />
          ) : filtered.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full text-center px-6 py-12">
              <FileQuestion size={28} className="text-slate-200 mb-3" />
              <p className="text-xs text-slate-400 leading-relaxed whitespace-pre-line">
                {replayMode
                  ? '回放进行中——拖动进度条或点击播放逐步重放历史调用'
                  : callEvents.length === 0
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
