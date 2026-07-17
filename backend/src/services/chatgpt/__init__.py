# -*- coding: utf-8 -*-
"""ChatGPT 上游接入（对齐 digital-platform--generator 的 codex 栈）。

两条上游，均配置门控、对 Claude 零影响：
- 订阅：`codex exec --json` 跑在本地 codex-runner 容器里，挂载账号池的 auth.json 登录态（services/chatgpt/codex.py）。
- API key：passthrough 到 api.openai.com/chat/completions（services/chatgpt/openai_api.py）。

网关按模型名（gpt-* / o[1345]* / chatgpt* / codex*）分流到这里（见 controller/gateway.py）。
provider.run() 优先订阅、其次 API key，产出与 runner.RunnerResult 同构的归一化结果。
"""
