from __future__ import annotations

import os

import yaml

DEFAULT_CONFIG = {
    "name": "Untitled task",
    "workspace": os.getcwd(),
    "mode": "normal",
    "agent": "opencode",
    "check_interval_minutes": 5,
    "goal": "Describe what you want the agent to accomplish.",
    "acceptance": ["All steps completed successfully"],
    "steps": ["Analyze the problem", "Implement the solution", "Verify the result"],
}

CONFIG_FILENAME = "openloom.yaml"


def generate_config(path: str | None = None) -> str:
    target = path or os.path.join(os.getcwd(), CONFIG_FILENAME)
    if os.path.exists(target):
        raise FileExistsError(f"{target} already exists")

    content = yaml.dump(DEFAULT_CONFIG, default_flow_style=False, sort_keys=False, allow_unicode=True)
    with open(target, "w") as f:
        f.write(content)
    return target
