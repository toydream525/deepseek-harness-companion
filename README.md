# DSH 伴航 · Harness Companion

简体中文 | [English](README.en.md)

**[下载 v0.1.0 Windows x64 安装包](https://github.com/toydream525/deepseek-harness-companion/releases/download/v0.1.0/DSH-Companion-Setup-v0.1.0-windows-x64.exe)** · [便携 ZIP](https://github.com/toydream525/deepseek-harness-companion/releases/download/v0.1.0/DSH-Companion-v0.1.0-windows-x64.zip) · [从源码构建](docs/OPEN_SOURCE_RELEASE.md) · [完整配置指南](resources/guides/从零配置指南.md)

DSH 伴航是管理本地 GGUF 模型、试聊和连接 DeepSeek Harness 的 Windows 桌面控制台。

![DSH 伴航主界面预览](docs/images/app-preview.png)

首页集中显示模型、运行状态和 DSH 连接，并提供启动与试聊入口；第二页「工作台」管理配置导入导出和分别控制模型、DSH。

这是独立社区项目，与 DeepSeek 没有隶属或背书关系。官方上游项目见 [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness)。

## 三步上手

1. **准备文件。** 运行 Windows 安装包，或下载并完整解压便携 ZIP 后启动 `DSH-Companion.exe`。到 [llama.cpp 官方发布页](https://github.com/ggml-org/llama.cpp/releases)选适合本机驱动的完整 Windows 引擎包（不确定时先用 CPU 版），保存到任意独立目录；也可在「组件更新」主动准备清单中已审核的版本。在伴航「设置与诊断 → 运行环境」选择其中的 `llama-server.exe`，直到显示检测可用。到「模型库与下载」添加已有 GGUF 或下载所需完整文件；成功时模型出现在库中且量化/大小可核对。要用 DSH，先从 [Node.js 官方站](https://nodejs.org/en/download)安装 Node.js 并选择 `node.exe`；再按 [DeepSeek Harness 官方项目](https://github.com/deepseek-ai/deepseek-harness)安装完整本地树，或在「组件更新」主动准备已审核的 DSH 版本并启用。单次 `npx` 临时运行不能代替本地组件绑定。
2. **选方案并导入。** 可自由选择其他 GGUF 和通用参数。内置 Qwen3.8 方案是**可选参考**：先取得 [ISTA 原版 IQ3_S](https://huggingface.co/ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF/tree/main)、[Huihui IQ3_S](https://huggingface.co/huihui-ai/Huihui-Qwen3.8-27B-abliterated-GGUF/tree/main) 两个完整 GGUF，以及复现稳定档必需的 [froggeric 固定版本](https://huggingface.co/froggeric/Qwen-Fixed-Chat-Templates/tree/855bffc) `chat_template.jinja`。文件放任意可访问目录，入库后点「一键导入原提示词配置」，按预览匹配本机资源；结果弹窗会列出导入的三个稳定预设。若再准备 Huihui `UD-IQ4_XS.gguf`，可一并导入第四个**未完成本机验收**的写作档；缺少它时写作档保持待绑定。其他模型可先用 GGUF 内嵌模板走通用方案。高级实验档位默认不启用。
3. **检查启动。** 在「工作台」选模型与合适的保守预设，启动并等待本地 API 显示就绪；在「试聊」生成一句话确认真实响应。要用 DSH，再连接已认证实例或启动自己安装的实例，测试本地提供方并设为新会话默认，开始新会话验证。只有模型试聊成功不代表 DSH 已配置完成。

模型权重、llama.cpp、Node.js 和 DeepSeek Harness 均不包含在发布包中；已有资源可复制到新目录后重新选择，无需重复下载。

## 选择一个 Qwen 参考模式

安装包和便携 ZIP 的 `scripts/deploy/` 内有四个模式入口。先从下表对应的 Hugging Face 页面**手动下载完整 GGUF**，并准备兼容的完整 [llama.cpp 引擎](https://github.com/ggml-org/llama.cpp/releases/tag/b11149)。固定版本的 [froggeric `chat_template.jinja`](https://huggingface.co/froggeric/Qwen-Fixed-Chat-Templates/tree/855bffc) 可先自行下载；未指定时，正式导入脚本会从该固定提交下载约 28 KB 模板并核验，`-DryRun` 不下载且要求模板已存在。脚本复用已有文件，绝不下载模型权重。也可以完全在应用内用「一键导入原提示词配置」，无需运行脚本。

| 模式入口 | 用途与状态 | 手动下载的 GGUF 文件 |
|---|---|---|
| [`Uncensored32K.cmd`](scripts/deploy/Uncensored32K.cmd) | 思考 32K，对应预设在原目标机器已测 | [Huihui `Huihui-Qwen3.8-27B-abliterated-GSQ-RCO-IQ3_S.gguf`](https://huggingface.co/huihui-ai/Huihui-Qwen3.8-27B-abliterated-GGUF/tree/main) |
| [`UncensoredDirect32K.cmd`](scripts/deploy/UncensoredDirect32K.cmd) | 关闭思考 32K，对应预设在原目标机器已测 | 同上 Huihui IQ3_S |
| [`Original32K.cmd`](scripts/deploy/Original32K.cmd) | 原版思考 32K，对应预设在原目标机器已测 | [ISTA `Qwen3.8-27B-GSQ-RCO-IQ3_S.gguf`](https://huggingface.co/ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF/tree/main) |
| [`Writing8K-unverified.cmd`](scripts/deploy/Writing8K-unverified.cmd) | 写作 8K，可选实验，**尚未完成推理验收** | [Huihui `Huihui-Qwen3.8-27B-abliterated-UD-IQ4_XS.gguf`](https://huggingface.co/huihui-ai/Huihui-Qwen3.8-27B-abliterated-GGUF/tree/main) |

双击对应 `.cmd` 后，按提示选择伴航 EXE、`llama-server.exe` 和**已手动下载的 GGUF 路径**；模板若已下载，可传入 `-TemplatePath` 复用。脚本只检查资源并导入所选单个预设，不启动模型；完成后在伴航中启动并试聊。命令行预检示例：`powershell.exe -NoProfile -File .\scripts\deploy\Deploy.ps1 -Mode Uncensored32K -DryRun`；缺少的路径会逐项询问，预检时请提供现有模板，去掉 `-DryRun` 才写入配置。它调用安装版 EXE，自身不要求额外安装 Python。四个档位使用原目标机器的 16 GB NVIDIA 显存、64 GB 内存和 CUDA 配置；脚本会拒绝不能确认达到条件的机器，请用应用内通用模型流程按硬件调整，勿强套这组参数。

如果 Windows 的 PowerShell 执行策略阻止脚本，先核对发布来源和脚本内容，再在**当前 PowerShell 窗口**自行运行 `Set-ExecutionPolicy -Scope Process RemoteSigned`，从同一窗口执行 `./scripts/deploy/Deploy.ps1` 并传入 `-Mode`；这只影响该窗口。组织策略仍禁止时使用应用内的配置导入入口，不修改全局策略。

原目标硬件为 Windows 11、i7-13700K、RTX 4080 16 GB 与 64 GB RAM；它只说明旧配方的参考环境，其他电脑请按实际内存/显存和驱动从保守参数开始。更完整的选文件、模板、代理和故障指引见 [从零配置指南](resources/guides/从零配置指南.md)。

<details>
<summary>复制给 AI 帮我配置</summary>

可以把[从零配置指南](resources/guides/从零配置指南.md)和下面任务一起交给 AI 协助。配置涉及下载、安装和本机路径选择，请在操作时核对来源与结果。

```text
请帮我在这台 Windows 电脑配置 DSH 伴航，并以随附《从零配置指南》为准。先检查系统、CPU/GPU、驱动、内存、显存和已有的 llama.cpp、GGUF、聊天模板、Node.js、DSH 文件；可用的已有资源优先复用或复制到独立目录，不重复下载，也不要清理、覆盖其他模型或程序。需要新文件时，只从指南列出的 llama.cpp、Node.js、DeepSeek Harness 官方来源及明确列出的模型/模板仓库取得，核对具体文件名和版本；按实际硬件选择 CPU 或兼容的 GPU 构建，不套用旧机器参数。安装和绑定后，在伴航里逐项确认引擎检测、模型入库、可选 Qwen 三个稳定预设的资源绑定与导入结果、可选第四写作档的待绑定状态、本地 API 就绪、试聊真实响应；若需 DSH，还要确认 Node/DSH 检测、认证连接、本地提供方、新会话默认与实际对话。不要读取、复制、展示或上传 API 密钥、认证链接、聊天记录和个人配置；需要用户输入凭据时只指示在应用安全输入框由用户填写。最后给我一份完成/未完成清单、实际所选文件与版本、发现的问题和仍需我决定的事项。高级实验参数不要默认启用。
```

</details>

## 完整配置

1. 在「设置与诊断 → 运行环境」按提示打开 [llama.cpp 官方发布页](https://github.com/ggml-org/llama.cpp/releases)，下载适合本机的 Windows 版本并解压。若不确定 GPU 版本，先选择 CPU；CUDA、Vulkan、ROCm 的候选只供选择，实际可用性以设备探测和推理为准。选择 `llama-server.exe` 或其文件夹，点击「检测并使用」。
2. 在「模型库与下载」添加已有 GGUF 文件，或从 Hugging Face 链接选择一个完整文件/分片组下载。下载完成会入库，不会自动启动。模型和聊天模板可以存放在任意可访问路径。
3. 如需复现原始 Qwen3.8 部署提示词，打开「从零配置」内置指南；将对应 GGUF 和 froggeric 模板入库后，在「工作台」或「参数预设」点击「一键导入原提示词配置」。资源齐全时直接导入稳定档位；缺失时选择本机文件并查看待绑定项。此操作不会下载、启动模型或覆盖现有自定义参数。64K 等高级实验档位不会自动启用。
4. 在「工作台」选模型与参数预设，点击启动；切换模型可用「切换并启动所选模型」。在「试聊」中可输入临时系统提示词，独立设置单次输出上限并查看生效参数。聊天历史只保留在本次程序内存中。
5. 要使用 DSH，在「设置与诊断 → DSH 组件」选择按 [DeepSeek Harness 官方项目](https://github.com/deepseek-ai/deepseek-harness)说明安装的现有目录及 [Node.js](https://nodejs.org/en/download) 的 `node.exe` 检测；若 DSH 尚未安装，也可先绑定 Node.js，再在「组件更新」主动准备并启用已审核版本。随后在「DSH 与提供方」连接已有认证实例或启动自有实例，管理本地及云端提供方。开始 DSH 工作会分别核实模型、DSH、提供方与新会话默认设置。外部启动的 DSH 不会在关闭本程序时被停止。

## 日常操作

- 默认最小化后留在任务栏；可在设置中开启收起到系统托盘。首次点击 X 关闭按钮时，可选择退出并停止本程序启动的模型及 DSH，或收起到托盘并让它们继续运行；可记住选择，之后仍能在设置中修改。外部启动的 DSH 保留运行。开机启动管理器、启动后加载模型和启动收托盘是三个独立且默认关闭的选项。
- 参数预设可单个或集合导入导出；导入前会预览差异并校验。导出文件不包含 API 密钥。若模板或模型路径在新电脑不存在，请重新绑定。
- 「导出当前配套配置」会将已绑定且调整过的原提示词整套档位保存为可迁移 JSON；新目录使用「导入本地配套配置包」匹配模型与模板。配置包不包含模型权重、机器绝对路径依赖、密钥、聊天或日志。
- 「设置与诊断 → 网络代理」支持直连、系统代理、自定义 HTTP/HTTPS 和 SOCKS5。Hugging Face 请求使用所选策略；本程序启动的 DSH 仅支持 HTTP/HTTPS 代理，设置变更后需重启其进程。外部启动的 DSH 应在其自身环境设置代理。本机管理接口始终直连。
- 「设置与诊断 → 组件更新」可由用户主动准备内置清单中已审核的 llama.cpp 引擎或 DSH 版本，核验后切换路径，失败时保留旧文件并可回滚；这不代表已检测到上游实时最新版。准备 DSH 时会在新目录运行锁定版本的 npm 安装及其依赖安装脚本，不在旧目录执行。所选外部安装不会被原地覆盖，运行中的模型或 DSH 不会被自动停止。Node.js 与显卡驱动只提供官方更新入口和更新后复检，不由本程序安装。诊断导出只含状态摘要，不导出认证密钥、链接、聊天或原始请求日志。

发布包中的 Python、PySide6/Qt 和下载依赖的许可证与来源见 `THIRD-PARTY-NOTICES.md`。发布 ZIP 不含个人配置、密钥、下载任务、模型权重或第三方引擎。

原创代码采用 [MIT 许可证](LICENSE)；第三方组件保留各自许可，详见 [第三方声明](THIRD-PARTY-NOTICES.md)。源码构建步骤和公开发布边界见 [开源发布指南](docs/OPEN_SOURCE_RELEASE.md)。旧部署报告中的 Qwen/引擎版本与性能仅是特定机器记录，不代表其他 Windows 设备已通过兼容测试。
