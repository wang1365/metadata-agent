import argparse
import sys
from datetime import datetime
from pathlib import Path

from deepseek_harness import DeepSeekHarness


ROOT = Path(__file__).resolve().parent


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="使用 sdk profile 调用阿里百炼")
    parser.add_argument("--check", action="store_true", help="仅验证运行时、模型路由和插件初始化")
    parser.add_argument("--smoke", action="store_true", help="验证一次模型调用，不请求使用工具")
    parser.add_argument("--model", default="deepseek-v4.1-flash")
    parser.add_argument("--prompt", help="执行指定任务，代替默认的两次数据库查询")
    args = parser.parse_args()

    workspace = ROOT / "workspace-1"
    workspace.mkdir(exist_ok=True)
    with DeepSeekHarness(
        provider="bailian",
        model=args.model,
        cwd=str(workspace),
        runtime_cwd=str(ROOT),
        dsh_home=str(ROOT / "dsh-home-1"),
        profile="sdk",
        initialize_timeout_seconds=60,
        request_timeout_seconds=180,
    ) as harness:
        print(f"SDK 初始化成功：profile=sdk, provider=bailian, model={args.model}")
        if args.check:
            return

        session_id = f"example-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
        prompts = [args.prompt] if args.prompt else [
            "只进行只读查询。帮我查询 auth_user 表里面有多少数据，并且租户为 2 的前 10 条用户的用户名是什么",
            "只进行只读查询。查询最新的用户登录日志，限制 10 条",
        ]
        if args.smoke:
            prompts = ["不要调用任何工具，只回复 BAILIAN_OK。"]

        for prompt in prompts:
            result = harness.run(prompt, session_id=session_id)
            print("finish_reason:", result.finish_reason)
            print("result:", result.final_response)
            if result.finish_reason != "completed":
                for event in result.events:
                    if event.get("type") == "turn/end":
                        print("turn/end:", event.get("data"))
                raise RuntimeError(f"模型任务未完成：{result.finish_reason}")
            if args.smoke and result.final_response.strip() != "BAILIAN_OK":
                raise RuntimeError("百炼调用已完成，但未返回预期的 BAILIAN_OK")

if __name__ == "__main__":
    main()
