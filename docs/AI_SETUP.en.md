# Ask an AI to configure DSH Companion

[Home and short copyable task](../README.en.md#ask-an-ai-to-configure-it) · [Detailed setup](GETTING_STARTED.en.md) · [简体中文](AI_SETUP.zh-CN.md)

Give the assistant the [full setup guide](GETTING_STARTED.en.md) and [built-in original recipe](../resources/guides/从零配置指南.md). The task below is an expanded execution checklist. **You download GGUF model weights yourself** from the Hugging Face page linked for the chosen mode; the AI should inspect and reuse files, not fetch duplicate model weights.

```text
Actually configure the current stable or installed DSH Companion version on this Windows computer. First check the actual app version, then read the matching README.en.md, docs/GETTING_STARTED.en.md, and resources/guides/从零配置指南.md in that version's repository. Operate the app and report observable results instead of only explaining instructions.

1. Inspect the actual OS, CPU/GPU, drivers, RAM/VRAM, and existing llama.cpp, GGUF, chat template, Node.js, and DSH installation. Do not assume this machine matches the RTX 4080 reference. Do not delete or overwrite older models or programs. Reuse sound files, or copy them to a separate directory when isolation helps.
2. For a missing engine, Node.js, or DSH, use only the official sources in the guide and select files suited to this computer. Do not download duplicates. Ask me to manually download the exact Qwen GGUF from the matching Hugging Face page in the README; then check completeness and shards and bind it in the app. Reuse the pinned froggeric template when already available.
3. Install or extract Companion, bind and probe llama-server.exe, and add the GGUF to the library. For the optional Qwen recipe, import the three stable presets and verify actual model/template paths. Leave the optional writing preset unbound if its file is absent; do not claim it passed inference. For other models, use the general settings with conservative context and memory use.
4. Start the model and obtain a real Trial Chat answer after the local API reports ready. Record the effective model, preset, context, output limit, real response, and meaningful errors. A file, process, or open port alone is not success.
5. If I want DSH, verify Node.js and a complete local DSH tree, connect to the current authenticated session, register the local provider, set it as the default for new sessions, and create an actual new conversation. Do not stop an external DSH without my direction. If authentication is missing, ask me to enter its original URL in the app; do not scrape external logs for tokens.
6. For each failure, give the concrete reason, impact, and next executable action. Finish with completed/incomplete items, selected file names and versions, reference-only claims, and decisions I still need to make. Keep advanced 64K/KV Streaming/MTP/vision experiments off unless I explicitly choose them; never mark untested features as passed.

Privacy: Do not read, copy, display, or upload API keys, DSH authentication URLs, chat history, personal configuration, or model weights. Ask me to enter credentials into the app. Do not disable authentication, expand the listen address, or change system-wide proxy or script policy for convenience.
```

At minimum, verify engine detection, complete GGUF, import result, and a real local answer. For DSH, also verify authentication, provider, new-session default, and a real conversation. The built-in recipe lists the longer original Qwen acceptance suite.
