import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI


# 明确指定项目根目录的 .env，避免自动查找路径异常
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env", override=False)


def get_ai_provider():
    return os.getenv("AI_PROVIDER", "deepseek").strip().lower()


def get_ai_model():
    provider = get_ai_provider()

    if provider == "deepseek":
        return os.getenv("DEEPSEEK_MODEL", "deepseek-flash")

    if provider == "openai":
        return os.getenv("OPENAI_MODEL", "gpt-5.5")

    raise ValueError(f"不支持的 AI 服务商：{provider}")


def get_ai_client():
    provider = get_ai_provider()

    if provider == "deepseek":
        api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
        if not api_key:
            raise ValueError("未配置 DEEPSEEK_API_KEY")

        return OpenAI(
            api_key=api_key,
            base_url="https://api.deepseek.com",
            timeout=60.0,
        )

    if provider == "openai":
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise ValueError("未配置 OPENAI_API_KEY")

        return OpenAI(
            api_key=api_key,
            timeout=60.0,
        )

    raise ValueError(f"不支持的 AI 服务商：{provider}")