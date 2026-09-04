import { ChatMessage, ToolStep, ChatTurn, TurnBlock } from '../types/rove';

/**
 * 判断是否为内部系统通知（例如 <inbox>、<background-results>、<reminder>）
 */
function isInternalNotice(content?: string): boolean {
  if (!content) return false;
  const trimmed = content.trim();
  return (
    trimmed.startsWith('<inbox>') ||
    trimmed.startsWith('<background-results>') ||
    trimmed.startsWith('<reminder>')
  );
}

/**
 * 将平铺的底层 LLM 消息流与工具调用聚合为高内聚的用户对话轮次 (ChatTurn)。
 * 解决原先一次工具调用被当成一条独立对话气泡展示的问题。
 */
export function groupMessagesIntoTurns(
  messages: ChatMessage[],
  activeToolSteps: ToolStep[] = [],
  currentStreamingText: string = ''
): ChatTurn[] {
  // 1. 构建工具返回值映射表 (tool_call_id -> output / is_error)
  const toolOutputMap = new Map<string, { output: string; is_error: boolean }>();
  for (const msg of messages) {
    if (msg.role === 'tool' && msg.tool_call_id) {
      const out = msg.content || '';
      const isError =
        out.trim().toLowerCase().startsWith('error') ||
        out.trim().toLowerCase().startsWith('exception') ||
        out.trim().toLowerCase().startsWith('traceback');
      toolOutputMap.set(msg.tool_call_id, { output: out, is_error: isError });
    }
  }

  const turns: ChatTurn[] = [];

  // 2. 遍历历史消息流，合并连续的助手发言与工具调用
  for (let i = 0; i < messages.length; i++) {
    const msg = messages[i];
    if (isInternalNotice(msg.content) || msg.role === 'tool') {
      continue;
    }

    if (msg.role === 'user') {
      turns.push({
        id: msg.id || `user-turn-${i}`,
        role: 'user',
        blocks: [{ type: 'text', content: msg.content || '' }],
      });
    } else if (msg.role === 'assistant') {
      let currentTurn: ChatTurn;
      const lastTurn = turns.length > 0 ? turns[turns.length - 1] : null;

      // 如果上一轮已经是助手发言，则无缝聚合到同一条对话卡片中，避免割裂为多个 Rove Lead 气泡
      if (lastTurn && lastTurn.role === 'assistant') {
        currentTurn = lastTurn;
      } else {
        currentTurn = {
          id: msg.id || `assistant-turn-${i}`,
          role: 'assistant',
          blocks: [],
        };
        turns.push(currentTurn);
      }

      // 若当前助手消息含有思考或前置文本说明，追加文本块
      if (msg.content && msg.content.trim()) {
        const lastBlock =
          currentTurn.blocks.length > 0
            ? currentTurn.blocks[currentTurn.blocks.length - 1]
            : null;
        if (lastBlock && lastBlock.type === 'text') {
          lastBlock.content = (lastBlock.content ? lastBlock.content + '\n\n' : '') + msg.content;
        } else {
          currentTurn.blocks.push({
            type: 'text',
            content: msg.content,
          });
        }
      }

      // 若当前助手消息触发了工具调用，追加工具步骤块，并关联执行输出
      if (msg.tool_calls && msg.tool_calls.length > 0) {
        for (const tc of msg.tool_calls) {
          const outInfo = toolOutputMap.get(tc.tool_id);
          currentTurn.blocks.push({
            type: 'tool',
            step: {
              tool_id: tc.tool_id,
              tool_name: tc.tool_name,
              tool_args: tc.tool_args,
              output: outInfo?.output,
              is_error: outInfo?.is_error ?? false,
              status: outInfo ? (outInfo.is_error ? 'error' : 'completed') : 'completed',
            },
          });
        }
      }
    }
  }

  // 3. 处理实时执行中的临时数据（当前正在运行的工具步骤与正在打字的流式文本）
  if (activeToolSteps.length > 0 || currentStreamingText) {
    const liveBlocks: TurnBlock[] = [];
    for (const step of activeToolSteps) {
      liveBlocks.push({
        type: 'tool',
        step,
      });
    }
    if (currentStreamingText) {
      liveBlocks.push({
        type: 'text',
        content: currentStreamingText,
      });
    }

    turns.push({
      id: 'assistant-turn-live',
      role: 'assistant',
      blocks: liveBlocks,
    });
  }

  return turns.filter((t) => t.blocks.length > 0);
}
