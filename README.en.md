<div align="center">
  <img src="manager/assets/companion-icon-128.png" alt="DSH Companion icon" width="88" />

  # DSH Companion

  **A simpler way to run local models alongside DeepSeek Harness on Windows.**

  [简体中文](README.md) · English

  `v0.1.0` · `Windows x64` · [`MIT`](LICENSE)

  **Installer preparing for release** · [Release page](https://github.com/toydream525/deepseek-harness-companion/releases) · **[Detailed setup](docs/GETTING_STARTED.en.md)** · [Project site](https://yuriaqua.com/harness)

  <small>For models and runtime requirements, see the <a href="docs/GETTING_STARTED.en.md">detailed setup guide</a>.</small>
</div>

![DSH Companion desktop](docs/images/app-preview.png)

Choose a GGUF model, start a local API, try a chat, and connect that model to DSH from one desktop app.

## What it does

| Feature | In the app |
|---|---|
| Local models | Add GGUF files, choose presets, start/stop, see readiness |
| Trial chat | Verify a real answer and inspect effective parameters |
| DSH integration | Detect or connect DSH, register a local provider, set the new-session default |
| Portable settings | Import/export presets and setup bundles; rebind missing files |

## Start in three steps

1. **Install the app.** Run the installer above; for the portable version, fully extract the ZIP before opening `DSH-Companion.exe`.
2. **Prepare components and a model.** Get a suitable engine from [official llama.cpp releases](https://github.com/ggml-org/llama.cpp/releases), select `llama-server.exe` in the app, and add a GGUF you downloaded from Hugging Face. For DSH, also prepare [Node.js](https://nodejs.org/en/download) and [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness).
3. **Start and verify.** Start the model on the home page and get a real answer in Trial Chat. If you use DSH, complete authenticated connection and provider setup, then verify a new conversation.

Read the [full getting started guide](docs/GETTING_STARTED.en.md), or give the [AI setup task](docs/AI_SETUP.en.md) to an AI assistant to work through on your computer.

## Ask an AI to configure it

Give the task below and the [detailed setup guide](docs/GETTING_STARTED.en.md) to an AI assistant that can operate your computer. Download model weights yourself from the Hugging Face pages in the table; the assistant can reuse existing files and verify each step.

<details>
<summary>Expand and copy the complete task</summary>

```text
Actually configure the current stable or installed DSH Companion version on this Windows computer; do not only give me a tutorial. Check the app's actual version and follow its matching docs/GETTING_STARTED.en.md and resources/guides/从零配置指南.md. Inspect the OS, CPU/GPU, drivers, RAM/VRAM, and existing llama.cpp, GGUF, chat template, Node.js, and DSH files. Reuse working resources instead of downloading duplicates or deleting old models. Ask me to download the chosen GGUF manually from its Hugging Face page linked in the README; check its completeness and bind it in the app. Obtain other missing components only from official sources in the guide. Select engine and settings for this hardware rather than copying the RTX 4080 reference values. Verify engine detection, model registration, optional Qwen setup import and path rebinding, local API startup, and a real trial-chat answer. If I want DSH, also verify authenticated connection, local provider registration, new-session default, and a real new conversation. Give a concrete failure reason and retry action for any failed stage. Do not read, print, or upload keys, DSH authentication links, chat history, or personal configuration; ask me to enter credentials in the app. Finish with completed/incomplete steps, chosen file names, versions and paths, and decisions still needed from me. Do not enable advanced experimental presets by default.
```

</details>

For a staged checklist and troubleshooting, see the [expanded AI setup task](docs/AI_SETUP.en.md).

## Optional Qwen 16GB VRAM recommended reference presets

Use **Import original prompt setup** inside the app, or choose a launcher in `scripts/deploy/`. Download only the GGUF for your chosen mode; the pinned template and settings are in the [detailed guide](docs/GETTING_STARTED.en.md#qwen-reference-setup).

1. Choose a mode below, **manually download its complete GGUF** from Hugging Face, and prepare a compatible llama.cpp engine and the template named in the guide. Reuse existing files when available.
2. Add the model under **Model Library & Downloads**, then use **Import original prompt setup** in Workbench. Bind this computer's model and template paths in the preview; missing resources are shown and can be supplied later.
3. Select the bound preset, start the model, and verify a real answer in Trial Chat. To use DSH, complete the home-page DSH connection and check a new conversation.

| Preset | Model type | Reasoning | Best for | Main tradeoff | Model download |
|---|---|---|---|---|---|
| [Thinking 32K](scripts/deploy/Uncensored32K.cmd) | Huihui abliterated variant | On | Complex code, analysis, multi-step tasks | Usually longer wait | [Huihui IQ3_S](https://huggingface.co/huihui-ai/Huihui-Qwen3.8-27B-abliterated-GGUF/tree/main) |
| [Fast 32K (Direct)](scripts/deploy/UncensoredDirect32K.cmd) | Huihui abliterated variant | Off | Everyday questions, rewriting | Use Thinking for complex tasks | [Huihui IQ3_S](https://huggingface.co/huihui-ai/Huihui-Qwen3.8-27B-abliterated-GGUF/tree/main) |
| [Original 32K](scripts/deploy/Original32K.cmd) | Original Qwen (not abliterated) | On | General tasks needing original behavior | Follows original response behavior more closely | [ISTA IQ3_S](https://huggingface.co/ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF/tree/main) |
| [Writing 8K](scripts/deploy/Writing8K-unverified.cmd) | Huihui abliterated variant | On | Writing, rewriting | Larger IQ4 file; 8K context | [Huihui UD-IQ4_XS](https://huggingface.co/huihui-ai/Huihui-Qwen3.8-27B-abliterated-GGUF/tree/main) |

<small>32K / 8K refer to context length. Abliterated does not mean zero refusals or higher accuracy. Reference hardware: NVIDIA 16GB VRAM and 64GB RAM; adjust other hardware with the <a href="docs/GETTING_STARTED.en.md#qwen-reference-setup">detailed guide</a>.</small>

## Documentation

| Goal | Open |
|---|---|
| Install and use the app | [Getting started and full setup](docs/GETTING_STARTED.en.md) |
| Qwen files, template, and settings | [Reference setup details](docs/GETTING_STARTED.en.md#qwen-reference-setup) |
| Fix a startup or connection problem | [Common problems](docs/GETTING_STARTED.en.md#common-problems) |
| Recreate the original Qwen deployment | [Built-in setup guide (Chinese)](resources/guides/从零配置指南.md) |
| Ask an AI to configure it | [AI setup task](docs/AI_SETUP.en.md) |
| Build and publish from source | [Open-source build guide](docs/OPEN_SOURCE_RELEASE.md) |
| Licenses and dependencies | [MIT](LICENSE) · [Third-party notices](THIRD-PARTY-NOTICES.md) |

DSH Companion is an independent community project, not affiliated with or endorsed by DeepSeek. [DeepSeek Harness upstream](https://github.com/deepseek-ai/deepseek-harness).
