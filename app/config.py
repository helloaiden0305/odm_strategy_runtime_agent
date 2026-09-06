"""全局配置。"""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"

# 从 .env 读取密钥等敏感配置(文件不存在时静默跳过)
load_dotenv(BASE_DIR / ".env")

DB_PATH = Path(os.getenv("DB_PATH", str(BASE_DIR / "data_odm_demo.db")))

# Agent Run 边界控制。旧环境变量仅用于平滑升级,新配置以 Agent Run / Turn 为准。
MAX_AGENT_TURNS = int(os.getenv("MAX_AGENT_TURNS", os.getenv("MAX_LOOP_STEPS", "6")))
AGENT_RUN_TIMEOUT_SECONDS = float(
    os.getenv("AGENT_RUN_TIMEOUT_SECONDS", os.getenv("LOOP_TIMEOUT_SECONDS", "90"))
)
MAX_PLAN_REPLANS = 1         # 单次运行最多一次重规划,避免规划空转
MAX_PLAN_STEPS = int(os.getenv("MAX_PLAN_STEPS", "3"))
MAX_TOOL_CALLS_PER_RUN = int(os.getenv("MAX_TOOL_CALLS_PER_RUN", "10"))
LOOP_CYCLE_REPEAT_THRESHOLD = int(os.getenv("LOOP_CYCLE_REPEAT_THRESHOLD", "3"))
LOOP_CYCLE_MAX_PATTERN_LENGTH = int(os.getenv("LOOP_CYCLE_MAX_PATTERN_LENGTH", "3"))
DEMO_TOOL_CIRCUIT_BREAKER_ENABLED = os.getenv("DEMO_TOOL_CIRCUIT_BREAKER_ENABLED", "false").lower() in {
    "1", "true", "yes", "on",
}
DEMO_TOOL_CIRCUIT_FAILURE_THRESHOLD = int(os.getenv("DEMO_TOOL_CIRCUIT_FAILURE_THRESHOLD", "2"))
DEMO_TOOL_CIRCUIT_COOLDOWN_SECONDS = float(os.getenv("DEMO_TOOL_CIRCUIT_COOLDOWN_SECONDS", "30"))
DEMO_TOOL_CIRCUIT_FAIL_TOOLS = frozenset(
    item.strip() for item in os.getenv("DEMO_TOOL_CIRCUIT_FAIL_TOOLS", "").split(",") if item.strip()
)
if MAX_AGENT_TURNS < 1:
    raise ValueError("MAX_AGENT_TURNS 必须至少为 1")
if AGENT_RUN_TIMEOUT_SECONDS <= 0:
    raise ValueError("AGENT_RUN_TIMEOUT_SECONDS 必须大于 0")
if MAX_PLAN_STEPS < 3:
    raise ValueError("MAX_PLAN_STEPS 必须至少为 3，以覆盖安全默认计划")
if MAX_TOOL_CALLS_PER_RUN < 1:
    raise ValueError("MAX_TOOL_CALLS_PER_RUN 必须至少为 1")
if LOOP_CYCLE_REPEAT_THRESHOLD < 2:
    raise ValueError("LOOP_CYCLE_REPEAT_THRESHOLD 必须至少为 2")
if LOOP_CYCLE_MAX_PATTERN_LENGTH < 1:
    raise ValueError("LOOP_CYCLE_MAX_PATTERN_LENGTH 必须至少为 1")
if DEMO_TOOL_CIRCUIT_FAILURE_THRESHOLD < 1:
    raise ValueError("DEMO_TOOL_CIRCUIT_FAILURE_THRESHOLD 必须至少为 1")
if DEMO_TOOL_CIRCUIT_COOLDOWN_SECONDS <= 0:
    raise ValueError("DEMO_TOOL_CIRCUIT_COOLDOWN_SECONDS 必须大于 0")

# 知识库检索:命中阈值(余弦相似度),低于此值视为"未命中"
KB_HIT_THRESHOLD = 0.45
KB_TOP_K = 3

# 策略样本库(playbook)语义检索:每次只召回最相关的 top-k 条样本,避免全量塞爆上下文
PLAYBOOK_TOP_K = int(os.getenv("PLAYBOOK_TOP_K", "5"))
PLAYBOOK_SCORE_THRESHOLD = float(os.getenv("PLAYBOOK_SCORE_THRESHOLD", "0.3"))

# GitHub Demo 默认每次启动清空运行态工单和指标,保留策略种子与设定。
RESET_DEMO_RUNTIME_ON_START = os.getenv("RESET_DEMO_RUNTIME_ON_START", "true").lower() in {
    "1", "true", "yes", "on"
}

# 选用的 LLM 提供方:mock(离线桩)| ark(真实豆包/火山引擎 ARK)
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "mock")
# 仅用于本机验收运行中断逻辑;默认 0,不影响常规 Demo。
MOCK_LLM_DELAY_SECONDS = float(os.getenv("MOCK_LLM_DELAY_SECONDS", "0"))

# 豆包(火山引擎 ARK)配置,OpenAI 兼容接口
ARK_API_KEY = os.getenv("ARK_API_KEY", "")
ARK_BASE_URL = os.getenv("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")
ARK_CHAT_MODEL = os.getenv("ARK_CHAT_MODEL", "")
# 豆包 embedding 模型(multimodal 接口),用于策略样本库语义检索
ARK_EMBED_MODEL = os.getenv("ARK_EMBED_MODEL", "doubao-embedding-vision-251215")
