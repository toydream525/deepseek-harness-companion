# DSH Companion · Harness Companion

[简体中文](README.md) | English

**[Download v0.1.0 Windows x64 installer](https://github.com/toydream525/deepseek-harness-companion/releases/download/v0.1.0/DSH-Companion-Setup-v0.1.0-windows-x64.exe)** · [Portable ZIP](https://github.com/toydream525/deepseek-harness-companion/releases/download/v0.1.0/DSH-Companion-v0.1.0-windows-x64.zip) · [Build from source](docs/OPEN_SOURCE_RELEASE.md) · [Detailed setup guide (Chinese)](resources/guides/从零配置指南.md)

DSH Companion is a Windows desktop console for managing local GGUF models,
testing chats, and connecting them to DeepSeek Harness.

![DSH Companion desktop preview](docs/images/app-preview.png)

The home page shows model and DSH status with the main start and chat actions.
The second Workbench page handles configuration import/export and separate
model and DSH controls.

This is an independent community project, not affiliated with or endorsed by
DeepSeek. The upstream project is [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness).

## Start in three steps

1. **Prepare the files.** Run the Windows installer, or download and fully
   extract the portable ZIP before opening `DSH-Companion.exe`. Get
   a complete build suitable for your hardware from the official
   [llama.cpp releases](https://github.com/ggml-org/llama.cpp/releases) (start
   with CPU if unsure), place it in any separate accessible folder, and select
   `llama-server.exe` under **Settings & Diagnostics → Runtime Environment**.
   The success signal is a usable engine detection. Add an existing complete
   GGUF file or download one through the model library; check its displayed
   size and quantization. You may instead explicitly prepare the reviewed
   engine version in **Component Updates**. Only for DSH use, install
   [Node.js](https://nodejs.org/en/download) and select `node.exe`; then
   either install the complete local tree using the
   [official DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness)
   instructions or explicitly prepare the pinned DSH version in **Component
   Updates**. A one-off `npx` run does not substitute for a selected local tree.
2. **Choose and import a setup.** Any suitable GGUF can use the general preset
   flow. The built-in Qwen3.8 setup is an **optional reference**. For its
   stable presets, obtain the complete
   [ISTA IQ3_S file](https://huggingface.co/ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF/tree/main),
   [Huihui IQ3_S file](https://huggingface.co/huihui-ai/Huihui-Qwen3.8-27B-abliterated-GGUF/tree/main),
   and the required `chat_template.jinja` from the
   [pinned froggeric revision](https://huggingface.co/froggeric/Qwen-Fixed-Chat-Templates/tree/855bffc).
   Store them wherever you choose. In **Import original prompt setup**, match
   the local files and check the result dialog for three stable presets. The
   fourth writing preset needs Huihui `UD-IQ4_XS.gguf`; it remains unbound if
   that file is absent and has not completed on-machine validation. Other
   models can use their embedded GGUF template in the general setup. Advanced
   experiments stay disabled by default.
3. **Start and verify.** Select a model and conservative preset, start it, and
   wait for the local API to report ready. Generate a short answer in the chat
   pane. If using DSH, also connect an authenticated instance or start your
   own installed one, test the local provider, set it as the default for new
   sessions, and verify a new conversation. A successful model chat alone does
   not mean DSH is configured.

The release does not include model weights, llama.cpp, Node.js, DeepSeek
Harness, or GPU runtimes. If you already have suitable files, copy them to a
new independent location and select their paths; you need not download them
again.

## Choose a Qwen reference mode

The installer and portable ZIP include four launchers in `scripts/deploy/`.
First **download the complete GGUF manually** from the matching Hugging Face
page below and prepare a compatible complete
[llama.cpp engine](https://github.com/ggml-org/llama.cpp/releases/tag/b11149).
You can download the pinned froggeric [`chat_template.jinja`](https://huggingface.co/froggeric/Qwen-Fixed-Chat-Templates/tree/855bffc)
yourself. If omitted during a real import, the script fetches that approximately
28 KB file from the fixed revision and verifies it; `-DryRun` never downloads
and requires an existing template. The scripts reuse existing files and never download model weights. You
can also use **Import original prompt setup** entirely in the app without a
script.

| Launcher | Purpose and status | GGUF file to download manually |
|---|---|---|
| [`Uncensored32K.cmd`](scripts/deploy/Uncensored32K.cmd) | Thinking 32K; source-host preset verified | [Huihui `Huihui-Qwen3.8-27B-abliterated-GSQ-RCO-IQ3_S.gguf`](https://huggingface.co/huihui-ai/Huihui-Qwen3.8-27B-abliterated-GGUF/tree/main) |
| [`UncensoredDirect32K.cmd`](scripts/deploy/UncensoredDirect32K.cmd) | Direct 32K; source-host preset verified | Same Huihui IQ3_S file |
| [`Original32K.cmd`](scripts/deploy/Original32K.cmd) | Original thinking 32K; source-host preset verified | [ISTA `Qwen3.8-27B-GSQ-RCO-IQ3_S.gguf`](https://huggingface.co/ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF/tree/main) |
| [`Writing8K-unverified.cmd`](scripts/deploy/Writing8K-unverified.cmd) | Optional writing experiment; **inference not validated** | [Huihui `Huihui-Qwen3.8-27B-abliterated-UD-IQ4_XS.gguf`](https://huggingface.co/huihui-ai/Huihui-Qwen3.8-27B-abliterated-GGUF/tree/main) |

Double-click a `.cmd` launcher and select the existing Companion EXE,
`llama-server.exe`, and manually downloaded GGUF when prompted. Pass
`-TemplatePath` to reuse an existing template. It checks the
resources and imports one selected preset; it does not start the model. Open
the app afterward to start it and verify a real chat. For a read-only check,
run `powershell.exe -NoProfile -File .\scripts\deploy\Deploy.ps1 -Mode Uncensored32K -DryRun`;
it prompts for missing paths and needs an existing template for this check.
Omit `-DryRun` to import. The script calls the packaged EXE and does not
require a separate Python installation. These presets retain the source
machine's 16 GB NVIDIA VRAM, 64 GB RAM, and CUDA settings. The script rejects
machines that cannot confirm those requirements. Use the app's general setup
with hardware-appropriate parameters on other systems.

If Windows PowerShell policy blocks the script, first inspect the release
source and script. You may explicitly run `Set-ExecutionPolicy -Scope Process RemoteSigned`
in your **current PowerShell window**, then invoke `./scripts/deploy/Deploy.ps1`
with `-Mode` from that same window; this setting lasts only for that window.
If an organization policy still blocks scripts, use the in-app import instead
of changing system-wide policy.

The previous reference machine used Windows 11, an i7-13700K, RTX 4080 16 GB,
and 64 GB RAM. Those settings are a historical baseline; choose conservative
values for other hardware. The [detailed setup guide](resources/guides/从零配置指南.md)
explains exact files, templates, proxy limits, and troubleshooting.

<details>
<summary>Copy this task for an AI to help me configure it</summary>

You can give an AI the [setup guide](resources/guides/从零配置指南.md) and this
task. Check download sources and results as it works on your machine.

```text
Help me configure DSH Companion on this Windows computer using the included setup guide. First inspect the actual CPU/GPU, drivers, RAM/VRAM, and any existing llama.cpp engine, GGUF models, chat template, Node.js, and DSH installation. Reuse usable files or copy them to an independent folder; do not download duplicates or delete or overwrite other models and programs. If files are missing, use only the official llama.cpp, Node.js, and DeepSeek Harness sources and the exact model/template repositories linked in the guide. Select an engine for this hardware instead of copying settings from the old test machine. Bind the chosen paths in the app, then confirm engine detection, model library entries, the optional Qwen three stable presets and their required template, the optional fourth writing preset's bound or pending state, local API readiness, and a real chat response. If DSH is requested, also confirm Node/DSH detection, authenticated connection, local provider test, new-session default, and an actual new conversation. Do not read, copy, display, or upload API keys, authentication links, chats, or personal configuration; ask me to enter credentials in the app myself. Finish with a completed/incomplete checklist, the chosen file names and versions, issues found, and decisions still needed from me. Keep advanced experiment profiles disabled unless I explicitly choose them.
```

</details>

## What the app covers

- Import GGUF models, download complete Hugging Face files or shard groups,
  inspect capabilities, and select reproducible parameter presets.
- Start and stop a local model server, try a chat with a temporary system
  prompt and an explicit per-request output limit, and expose a local API when
  you choose. Chat history stays in the current process memory.
- Import the bundled Qwen3.8 companion recipe after binding its two stable
  models and required froggeric template. It does not download or start anything automatically;
  experimental larger-context profiles stay disabled until you choose them.
- Connect an existing authenticated DSH instance or manage one you installed.
  DSH processes started outside this app remain under external control.
- The opt-in Component Updates page can prepare pinned, reviewed llama.cpp or
  DSH versions in separate directories, verify them, switch the selected path,
  and roll back. It does not claim to track the newest upstream release or
  overwrite an external installation. Running services are never stopped by
  an update. DSH preparation runs integrity-locked npm dependencies and their
  install scripts only in the new managed directory. Node.js and GPU drivers
  have official update links and a rescan, with no in-app installer.

By default, minimizing leaves the app in the taskbar; tray behavior can be enabled
in Settings. The first click on X asks whether to exit and stop models and DSH
started by this app, or minimize to the tray and keep them running. The choice
can be remembered and changed later in Settings. An external DSH remains running.
Autostart options are separate and off by default. Hugging Face downloads support direct, system, HTTP/HTTPS,
and SOCKS5 proxy choices. DSH started by the app supports HTTP/HTTPS proxy
configuration only, while an externally started DSH uses its own settings.
Local management requests bypass the proxy.

The built-in Chinese guides cover installation, model choice, the original
Qwen recipe, and optional advanced experiments. Past Qwen/engine performance
figures describe one tested machine, not a compatibility guarantee for other
Windows systems.

Original application code is [MIT-licensed](LICENSE). Bundled components
retain their own terms, documented in
[third-party notices](THIRD-PARTY-NOTICES.md). The
[open-source release guide](docs/OPEN_SOURCE_RELEASE.md) explains how to
build and what may be published.
