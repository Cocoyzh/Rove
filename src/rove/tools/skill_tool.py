from ..skill_loader import SkillLoader
from ..tool_registry import Tool


def build_skill_tool(skill_loader: SkillLoader) -> Tool:
    return Tool(
        name="load_skill",
        description="Load specialized data analysis knowledge by skill name.",
        input_schema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Skill name to load, such as outlier_detection or evidence_based_report_writing.",
                }
            },
            "required": ["name"]
        },
        handler=lambda **kw: skill_loader.get_content(kw["name"])
    )