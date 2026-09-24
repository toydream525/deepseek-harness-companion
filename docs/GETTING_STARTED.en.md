# DSH Companion: getting started and full setup

[Home](../README.en.md) · [简体中文](GETTING_STARTED.zh-CN.md) · [AI setup task](AI_SETUP.en.md)

This guide starts from a fresh configuration. The app manages local GGUF models and DeepSeek Harness (DSH) connections. The release includes the desktop app, but not model weights, llama.cpp, Node.js, DSH, or GPU runtimes. You can select existing files or copy them to a separate folder instead of downloading them again.

## 1. Install the desktop app

Get the Windows x64 Setup from [v0.1.0 Releases](https://github.com/toydream525/deepseek-harness-companion/releases/tag/v0.1.0), or fully extract the portable ZIP before running `DSH-Companion.exe`. Do not run inside the ZIP. The first-use explanation can be dismissed permanently and reopened in Settings. Windows startup and automatic model loading are off by default.

## 2. Prepare an engine and a GGUF

Download a complete Windows engine archive from [official llama.cpp releases](https://github.com/ggml-org/llama.cpp/releases). Choose for your actual GPU and driver; start with CPU if unsure. Extract it and select `llama-server.exe` under **Settings & Diagnostics → Runtime Environment**. Actual device detection and inference determine whether CUDA, Vulkan, or ROCm works. A full CUDA Toolkit is not universally required.

Download a complete GGUF yourself from Hugging Face, or add an existing file under **Model Library & Downloads**. Split models need every shard; a projector cannot be started as a standalone text model. Adding a model does not start it. Model and template paths may contain spaces.

Select a model and preset in **Workbench** and start it. Once the home page reports the API ready, request a real answer in **Trial Chat**. A library entry or running process alone is not an inference test. The local API address is selectable and copyable under **Settings & Diagnostics → API & Network**.

## Qwen reference setup

The original recipe came from Windows 11, i7-13700K, RTX 4080 16 GB, and 64 GB RAM. These are **recommended reference presets targeting NVIDIA 16GB VRAM and 64GB system RAM**, not a guarantee for every 16GB VRAM device; other GGUF models remain supported. Adapt settings to actual hardware. See the [built-in original setup guide](../resources/guides/从零配置指南.md) and [advanced experiment route](../resources/guides/进阶实验路线.md) for the original target and later experiments.

| Mode | Exact file to download manually | Hugging Face | Status |
|---|---|---|---|
| Huihui thinking 32K | `Huihui-Qwen3.8-27B-abliterated-GSQ-RCO-IQ3_S.gguf` | [Huihui repository](https://huggingface.co/huihui-ai/Huihui-Qwen3.8-27B-abliterated-GGUF/tree/main) | Tested on source machine |
| Huihui direct 32K | Same Huihui IQ3_S file | [Huihui repository](https://huggingface.co/huihui-ai/Huihui-Qwen3.8-27B-abliterated-GGUF/tree/main) | Tested on source machine |
| Original thinking 32K | `Qwen3.8-27B-GSQ-RCO-IQ3_S.gguf` | [ISTA repository](https://huggingface.co/ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF/tree/main) | Tested on source machine |
| Writing 8K | `Huihui-Qwen3.8-27B-abliterated-UD-IQ4_XS.gguf` | [Huihui repository](https://huggingface.co/huihui-ai/Huihui-Qwen3.8-27B-abliterated-GGUF/tree/main) | Optional; inference not validated |

Stable reference presets also require `chat_template.jinja` from the [pinned froggeric revision 855bffc](https://huggingface.co/froggeric/Qwen-Fixed-Chat-Templates/tree/855bffc). Register the model and template, then use **Import original prompt setup** in Workbench or Parameter Presets and match local paths in the preview. The missing writing model can remain unbound. Importing does not download or start model weights or replace custom presets. For another model, start with its embedded GGUF template and general setup.

The release also contains four [mode launchers](../scripts/deploy/). Double-click the chosen `.cmd` and select the Companion EXE, `llama-server.exe`, and the GGUF you downloaded manually. The launcher imports only that preset; it never downloads model weights or starts a model. Pass `-TemplatePath` to reuse a local template. Without it, a real import can fetch the approximately 28 KB template from the pinned revision and verify it; `-DryRun` never downloads and requires an existing template. For a read-only check:

```powershell
powershell.exe -NoProfile -File .\scripts\deploy\Deploy.ps1 -Mode Uncensored32K -DryRun
```

The launcher calls the packaged EXE, so it needs no separate Python. It rejects machines whose original hardware conditions cannot be confirmed; use the app's general preset flow on other systems. If PowerShell policy blocks the script, inspect its source first. You may use `Set-ExecutionPolicy -Scope Process RemoteSigned` in the **current window only**, or use the in-app import instead of changing global policy.

## 3. Connect DeepSeek Harness

Only DSH users need [official Node.js](https://nodejs.org/en/download) and [DeepSeek Harness upstream](https://github.com/deepseek-ai/deepseek-harness). Install a complete local DSH tree following upstream instructions; a one-off `npx` run is not a selected local installation. You can also explicitly prepare the app's reviewed pinned version under Component Updates.

Under **Settings & Diagnostics → DSH Components**, select the DSH folder and `node.exe` until both pass detection. Under **DSH & Providers**, start an app-managed DSH or paste the original authenticated URL of an existing external instance. The app cannot obtain an external instance's token automatically. After starting a local model, the home-page one-click action checks model readiness, DSH, local provider registration, and the default for new sessions. You can also perform each step on the DSH page. Finally create a new DSH conversation and verify it works. A successful Trial Chat alone does not prove DSH integration.

Under **DSH & Providers**, you can add, edit, or delete local-model and cloud-API providers, including their API URL, protocol, and model list. Enter and save access keys in the app; saved keys are not displayed again. After a connection test, select a model and set it as the **default for future new sessions**. Existing DSH conversations are not rewritten; create a new one to verify the choice.

Local API, DSH page, and verified authenticated links are selectable and copyable in their pages. The authenticated link contains a token; use it only in a trusted context. Diagnostic exports exclude it. Closing the app cleans up DSH instances started by the app, while external instances remain running.

## Daily use and migration

- By default, minimizing keeps the taskbar entry. Settings can enable minimizing to tray. On the first X click, choose exit and stop app-owned services, keep them running in tray, or cancel. You can remember the choice and change it later. Windows startup, loading a model at launch, and starting in the tray are separate controls.
- Import/export parameter presets or the original-prompt setup bundle. Preview differences and rebind model/template paths on a new machine. Bundles exclude weights, keys, chats, and logs.
- Proxy choices include direct, system, HTTP/HTTPS, and SOCKS5 for Hugging Face. DSH started by the app supports HTTP/HTTPS proxy only; an external DSH uses its own environment. Local control requests bypass the proxy.
- Component Updates can prepare pinned reviewed llama.cpp or DSH versions in new folders and retain rollback. It does not track the latest upstream release. Node.js and GPU drivers get official links and a rescan, not in-app installation.
- Trial Chat history and its temporary system prompt remain in process memory. Diagnostic exports contain status summaries. Larger context, KV Streaming, MTP, and vision are optional experiments; see the [advanced route](../resources/guides/进阶实验路线.md).

## Common problems

| Symptom | Check first |
|---|---|
| Engine not detected | Is `llama-server.exe` from a fully extracted archive, and does device/help probing work? |
| Model does not start | Is the GGUF complete, are all shards present, and does the preset fit available RAM/VRAM? |
| API works but DSH does not | Check Node/DSH, authenticated connection, local provider, and new-session default separately. |
| External DSH needs authentication | Paste that process's original authenticated launch URL; the app does not scrape external logs for tokens. |
| Script import fails | Check actual engine/model/template paths; use in-app import if policy blocks scripts. |
| Download or update fails | Check network/proxy mode; keep or roll back to the previous component. |

When sharing diagnostics, keep the first real error but hide keys and authentication links. See [MIT](../LICENSE) and [third-party notices](../THIRD-PARTY-NOTICES.md).
