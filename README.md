<div align="center">

<img src="manager/assets/companion-icon-128.png" alt="DSH 伴航图标" width="92" height="92" />

# DSH 伴航

**本地模型与多前端控制台，连接 DeepSeek Harness 与 Pi WebUI**

<p>
  <strong>简体中文</strong> &nbsp;|&nbsp; <a href="./README.en.md">English</a>
</p>

<p>
  <a href="https://github.com/toydream525/deepseek-harness-companion/releases/tag/v0.2.0"><img src="https://img.shields.io/badge/Release-v0.2.0-blue.svg?style=flat-square" alt="Release v0.2.0" /></a>
  <img src="https://img.shields.io/badge/Platform-Windows%20x64-0078d4.svg?style=flat-square" alt="Windows x64" />
  <a href="./LICENSE"><img src="https://img.shields.io/badge/License-MIT-green.svg?style=flat-square" alt="License: MIT" /></a>
  <a href="https://yuriaqua.com/harness"><img src="https://img.shields.io/badge/Website-yuriaqua.com-5b6cf9.svg?style=flat-square" alt="Website" /></a>
</p>

<p>
  <a href="https://github.com/toydream525/deepseek-harness-companion/releases/download/v0.2.0/DSH-Companion-Setup-v0.2.0-windows-x64.exe"><strong>⬇️ 下载 Windows 安装版</strong></a> &nbsp;·&nbsp;
  <a href="https://github.com/toydream525/deepseek-harness-companion/releases/download/v0.2.0/DSH-Companion-v0.2.0-windows-x64.zip">📦 便携 ZIP</a> &nbsp;·&nbsp;
  <a href="./docs/GETTING_STARTED.zh-CN.md">📖 详细配置说明</a> &nbsp;·&nbsp;
  <a href="https://yuriaqua.com/harness">🌐 项目主页</a>
</p>

</div>

---

<div align="center">
  <img src="docs/images/app-preview.png" alt="DSH 伴航真实界面" width="880" />
</div>

> [!NOTE]
> **独立社区开源项目声明：** DSH 伴航是独立社区项目，与 DeepSeek 没有隶属或背书关系。独立启动 GGUF 本地 API、试聊，需要时再管理 DSH 提供方。Pi WebUI 来自社区 [xing-shuyin/pi-web-ui](https://github.com/xing-shuyin/pi-web-ui)；Harness Desktop 为 [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) 官方程序。

---

## ✨ 核心能力

| 模块 | 在软件里完成 |
| :--- | :--- |
| **🧠 本地模型** | 添加 GGUF、选择预设、启动 / 停止、实时查看就绪状态 |
| **💬 快速试聊** | 验证真实回答，直观查看生效参数 |
| **🔌 Harness 提供方与 API** | 检测或连接 DSH；添加、编辑、删除本地与云端提供方，配置 API 地址、接口协议与模型列表，安全保存密钥，并设置后续新会话默认模型 |
| **📦 配置迁移** | 一键导入 / 导出预设与配套配置，缺少文件时引导重新绑定 |

---

## 🚀 三步快速开始

1. **安装软件** — 运行上方安装包；使用便携版时，请先完整解压 ZIP，再打开 `DSH-Companion.exe`。
2. **准备组件与模型** — 从 [llama.cpp 官方发布页](https://github.com/ggml-org/llama.cpp/releases) 获取适合本机的引擎并在软件中选择 `llama-server.exe`；从 Hugging Face 自行下载 GGUF 并添加到模型库。需要 DSH 时，再准备 [Node.js](https://nodejs.org/en/download) 与 [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness)。
3. **启动并验证** — 在首页独立启动本地模型，到「试聊」验证真实回答。DSH、Pi WebUI 与 Harness Desktop 均为可选前端，各自独立管理。

> 首次使用请阅读 [📖 入门与完整配置](docs/GETTING_STARTED.zh-CN.md)，也可将 [🤖 AI 代配任务](docs/AI_SETUP.zh-CN.md) 交给 AI 在本机逐步完成。

---

## 🤖 交给 AI 自动配置

将下方完整任务连同 [详细配置说明](docs/GETTING_STARTED.zh-CN.md) 一并交给可操作本机的 AI 助手。模型权重需由你从下表 Hugging Face 页面自行下载；AI 会复用已有文件并逐步核验结果。

<details>
<summary><strong>展开并复制完整 AI 代配任务提示词</strong></summary>

```text
请在这台 Windows 电脑实际配置当前稳定版或已安装版 DSH 伴航，不要只给教程。先核对软件实际版本并使用与之匹配的仓库 docs/GETTING_STARTED.zh-CN.md 和 resources/guides/从零配置指南.md：检查系统、CPU/GPU、驱动、内存、显存和已有的 llama.cpp、GGUF、聊天模板、Node.js、DSH；可用资源优先复用，不重复下载或清理旧模型。让我从 README 对应的 Hugging Face 页面自行下载所选 GGUF；你检查完整性并在软件里绑定。缺少其他组件时使用文档列出的官方来源，按实际硬件选引擎和参数，不照搬 RTX 4080 参考值。完成引擎检测、模型入库、可选 Qwen 方案导入与路径重绑、本地 API 启动和真实试聊；若我要用 DSH，再验证认证连接、本地提供方、设为新会话默认以及实际新会话。任何一步失败都给出具体原因和可重试动作。不要读取、输出或上传密钥、DSH 认证链接、聊天记录与个人配置；凭证由我在应用内输入。最后列出已完成、未完成、实际文件版本与路径、仍需我决定的事项；不要默认启用高级实验档。
```

</details>

> 需要分阶段验收表和故障排查指引时，请打开 [AI 代配详细版](docs/AI_SETUP.zh-CN.md)。

---

## ⚙️ 可选：一键导入 Qwen 16GB 显存推荐参考方案

软件内支持「一键导入原提示词配置」，也可直接使用发布包的 `scripts/deploy/` 模式入口。只需自行下载所选档位的 GGUF；固定模板与参数详见 [Qwen 参考方案说明](docs/GETTING_STARTED.zh-CN.md#qwen-参考方案)。

1. 按下表选档位，从对应 Hugging Face 页面**手动下载完整 GGUF**，准备兼容的 llama.cpp 引擎与指南所列模板（已有文件直接复用）。
2. 在「模型库与下载」添加文件，随后到「工作台」点击「一键导入原提示词配置」，在预览中绑定本机模型与模板路径；缺少资源时会提示，可稍后补齐。
3. 选定已绑定预设、启动模型并在「试聊」验证；如需接入 DSH，可从首页完成本地提供方接入并在新会话验证。

| 方案 | 模型类型 | 思考 | 适合用途 | 主要取舍 | 模型下载 |
| :--- | :--- | :---: | :--- | :--- | :--- |
| **[思考 32K](scripts/deploy/Uncensored32K.cmd)** | Huihui 去限制版 | 开启 | 复杂代码、分析、多步任务 | 等待通常更久 | [Huihui IQ3_S](https://huggingface.co/huihui-ai/Huihui-Qwen3.8-27B-abliterated-GGUF/tree/main) |
| **[快速 32K（Direct）](scripts/deploy/UncensoredDirect32K.cmd)** | Huihui 去限制版 | 关闭 | 日常问答、改写 | 复杂任务建议切思考档 | [Huihui IQ3_S](https://huggingface.co/huihui-ai/Huihui-Qwen3.8-27B-abliterated-GGUF/tree/main) |
| **[原版 32K](scripts/deploy/Original32K.cmd)** | Qwen 原版（非去限制版） | 开启 | 保留原版行为的通用任务 | 相对去限制版更偏原版响应策略 | [ISTA IQ3_S](https://huggingface.co/ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF/tree/main) |
| **[写作 8K](scripts/deploy/Writing8K-unverified.cmd)** | Huihui 去限制版 | 开启 | 写作、改写 | IQ4 文件更大；上下文为 8K | [Huihui UD-IQ4_XS](https://huggingface.co/huihui-ai/Huihui-Qwen3.8-27B-abliterated-GGUF/tree/main) |

> <sub>32K / 8K 指上下文长度；“去限制版”不代表零拒答或更准确。参考硬件：NVIDIA 16GB 显存、64GB 内存；其他硬件请参照 <a href="docs/GETTING_STARTED.zh-CN.md#qwen-参考方案">详细指南</a> 调整。</sub>

---

## 📚 文档索引

| 想做什么 | 打开这里 |
| :--- | :--- |
| 第一次安装与日常使用 | [入门与完整配置](docs/GETTING_STARTED.zh-CN.md) |
| Qwen 文件、模板与完整配置 | [参考方案细节](docs/GETTING_STARTED.zh-CN.md#qwen-参考方案) |
| 启动或连接故障排查 | [常见问题](docs/GETTING_STARTED.zh-CN.md#常见问题) |
| 复现原始 Qwen 部署流程 | [内置从零配置指南](resources/guides/从零配置指南.md) |
| 让 AI 协助完成配置 | [AI 代配任务](docs/AI_SETUP.zh-CN.md) |
| 从源码构建与发布 | [开源构建说明](docs/OPEN_SOURCE_RELEASE.md) |
| 开源许可与第三方组件 | [MIT License](LICENSE) · [第三方声明](THIRD-PARTY-NOTICES.md) |

---

<div align="center">
  <sub>独立开源项目 · <a href="LICENSE">MIT License</a> · <a href="https://yuriaqua.com/harness">YuriAqua</a></sub>
</div>
