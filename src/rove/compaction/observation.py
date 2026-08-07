from typing import List, Literal, Dict, Any
from dataclasses import dataclass, field
import uuid

Status = Literal["success", "failure", "partial"]

@dataclass
class Artifact:
    name: str
    path: str
    size: int | None = None

@dataclass
class ObservationCard:
    obs_id: str
    tool_name: str
    status: Status
    purpose: str
    result: str
    input_summary: str
    data: Dict[str, Any] | None = None
    artifacts: List[Artifact] = field(default_factory=list)

    def to_prompt(self) -> str:
        """转成LLM可读的文本格式，用于嵌入压缩后的上下文"""
        lines = [f"[#{self.obs_id}] {self.tool_name}: {self.result}"]
        if self.status != "success":
            lines[0] += f" ⚠ {self.status}"
        if self.data:
            lines.append(f" Data: {str(self.data)[:300]}")
        for a in self.artifacts:
            lines.append(f" ->{a.path}")
        return "\n".join(lines)