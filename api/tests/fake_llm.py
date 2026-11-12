from collections.abc import Iterator

from app.agent.llm import LLMResponse, ToolCall, Usage


class FakeLLM:
    """依序吐出預先寫好的回應：字串是最終回答，list 是要呼叫的工具 [(name, args)]。記下每次收到的模型與動態提示。"""

    provider = "fake"

    def __init__(self, script: list) -> None:
        self.script: Iterator = iter(script)
        self.calls: list[dict] = []
        self.last_results: list[str] = []

    def chat(self, model, system_static, system_dynamic, messages, tools, effort, max_tokens=4096):
        self.calls.append({"model": model, "tools": [t.name for t in tools], "dynamic": system_dynamic})
        step = next(self.script)
        if isinstance(step, str):
            return LLMResponse(step, [], {"role": "assistant", "content": step}, Usage(100, 20), "end_turn", model)
        calls = [ToolCall(f"t{i}", n, a) for i, (n, a) in enumerate(step)]
        return LLMResponse("", calls, {"role": "assistant", "content": "tool"}, Usage(100, 10), "tool_use", model)

    def user(self, t):
        return {"role": "user", "content": t}

    def assistant_text(self, t):
        return {"role": "assistant", "content": t}

    def system_note(self, t):
        return {"role": "system", "content": t}

    def tool_results(self, results):
        self.last_results = [out for _, out, _ in results]
        return [{"role": "user", "content": "results"}]
