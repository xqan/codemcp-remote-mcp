# codemcp-remote v0.1.0 macOS 双架构 CLI 实施计划

[English canonical](../implementation-plan.md)

> 更新日期：2026-08-31
> 工作分支：`codex/macos-cli-packaging`
> 状态：**PHASE 1 COMPLETED / PHASE 2 COMPLETED / PHASE 3 GITHUB NATIVE GATE PASS / PHASE 4 INTEL64 LIVE GATE PASS / ARM64 REAL-HOST GATE PENDING / RELEASE BLOCKED**
> 冻结前序计划：[`../plans/v0.1.0/windows-release-baseline-2026-08-28.md`](../plans/v0.1.0/windows-release-baseline-2026-08-28.md)

本文件是独立中文实施计划；英文 `docs/implementation-plan.md` 为 canonical。Intel64 真机验收已经完成，但 macOS 整体支持与同一 `v0.1.0` 发布仍需等待真实 Apple Silicon 主机完成对应最终门禁，不能因为 Intel64 已通过就提前宣称双架构发布完成。

# Goal

为 `v0.1.0` 增加两个原生 macOS CLI 发布物：

```text
codemcp-remote-v0.1.0-macos-arm64.tar.gz
codemcp-remote-v0.1.0-macos-intel64.tar.gz
```

两个压缩包都必须只包含一个顶层目录。操作者解压后看到的稳定契约为：

```text
codemcp-remote/
├── codemcp-remote
├── codemcp-install.sh
├── codemcp-start.sh
├── codemcp-stop.sh
├── config/
├── LICENSE
├── THIRD_PARTY/
├── SHA256SUMS.txt
└── BUILD_PROVENANCE.json
```

为保持现有 PyInstaller `onedir` 生命周期语义，压缩包还允许且只允许一个隐藏的内部实现目录：

```text
codemcp-remote/.codemcp-runtime/
```

它承载冻结 Python 运行时、扩展模块，以及打包的 `cloudflared`。该目录不是公共配置面，不能由启动脚本加入任意可执行搜索路径，也不能存放可写状态、用户配置或密钥。如果“目录中绝对不能存在任何隐藏条目”是硬约束，必须在 Phase 2 前重新决策；本计划不选择 PyInstaller `onefile`，因为它会为每个后台 self-spawn 解包临时运行时，并显著复杂化 PID 所有权、停止与清理语义。

完成定义：

- `macos-arm64` 是原生 `arm64` thin Mach-O，可在 Apple Silicon Mac 上运行；
- `macos-intel64` 是原生 `x86_64` thin Mach-O；`intel64` 只用于用户指定的文件名；
- 安装后运行不要求 Python、`uv`、PowerShell 或 Homebrew；
- Git 仍是明确运行时前置条件；
- 解压后可运行 `./codemcp-install.sh` 完成交互式首次配置；脚本逐项收集必要值，通过正式 CLI 校验并在用户态 home 自动生成配置；
- 保持现有 22-tool MCP、安全策略、Git checkpoint/CAS 和 ChatGPT-only reasoning boundary；
- `init/project/start/status/stop/doctor`、本地 worker 和 Cloudflare 路径在两种架构上闭环；
- 密钥可安全持久化到 macOS Keychain，绝不退化成明文文件或命令行参数；
- 最终产物使用 ad-hoc 签名、不做 Apple notarization，并在 provenance、安装指南和验收证据中明确这一限制；
- 完成内部/外部 SHA-256、许可证清单和供应链审计；
- 在真实 Apple Silicon Mac 和真实 Intel Mac 上完成 clean-machine 验收，并重新执行受共享代码影响的 Windows 回归；不绑定特定 M 系列代际，最终支持声明以实际验收 hardware/OS evidence 为准。

macOS `v0.1.0` 只打包并支持 `cloudflared`。现存 OpenAI Tunnel provider 不在本次删除范围内，但不打包 `tunnel-client`，也不把 PATH 中的 `tunnel-client` 作为 macOS 发布契约或验收路径。

# Current Architecture

## 已验证事实

1. `bridge/pyproject.toml` 定义 Python 3.12+ 项目、版本 `0.1.0` 和两个 CLI entry point；运行依赖由 `bridge/uv.lock` 锁定。
2. `scripts/windows_entrypoint.py` 只是调用 `codemcp_bridge.main:main` 的通用冻结入口，虽名称带 Windows，macOS 构建可以直接复用，避免改变已验收的 Windows 打包入口。
3. `bridge/src/codemcp_bridge/main.py` 在冻结模式下：
   - 从 `sys.executable` 的父目录解析 distribution root；
   - 无参数时默认执行 `start`；
   - macOS packaged 默认可写 runtime home 使用 `~/Library/Application Support/codemcp-remote`，distribution root 保持可移动、非持久状态目录。
4. `bridge/src/codemcp_bridge/lifecycle.py` 管理 `init/start/status/stop/doctor`，并通过 self-spawn 启动 `serve` 与 `_tunnel`：

   ```text
   codemcp-start.sh
     -> codemcp-remote start
     -> lifecycle.start_services
     -> codemcp-remote serve
     -> codemcp-remote _tunnel
     -> cloudflared
   ```

5. worker 路径已经具备 POSIX 基础：冻结程序通过 `codemcp-remote _worker` 启动固定版 `codemcp==0.3.0`；`native_codemcp_worker.py` 的 Windows 补丁在非 Windows 上不安装。
6. Phase 1 已关闭早期 macOS 生命周期阻塞项：
   - macOS 持久密钥走 Keychain backend，失败时 fail closed，不退化为明文；
   - POSIX 后台进程使用独立 session/process group；
   - 运行状态记录稳定 process-start identity，并在停止/恢复前重新验证，防止 PID reuse/foreign-process termination。
7. `CloudflareTunnelProvider` 已支持 distribution-bundled `cloudflared`，macOS 两架构 release input 由固定版本/URL/SHA-256 manifest 管理；`OpenAITunnelProvider` 继续保留为兼容 provider，但不属于 macOS release payload/acceptance contract。
8. `scripts/build-windows-exe.ps1` 已建立可复用的发布原则：clean worktree、精确 source commit、校验过的 PyInstaller 工具闭包、`onedir`、冻结 worker smoke、许可证、`BUILD_PROVENANCE.json` 和 SHA-256。
9. 已存在独立 macOS release workflow，使用 `macos-15` 原生 arm64 与 `macos-15-intel` 原生 x86_64 runner 构建两个 candidate；Phase 3 GitHub native/convergence gate 已 PASS。
10. macOS 已作为同一 `v0.1.0` 的 additive release target 进入现行实施/验收范围。Intel64 final unsigned-candidate gate 已 PASS，Apple Silicon real-host gate 仍待完成；旧 Windows RC 或单一 Intel64 证据均不能单独证明双架构 macOS Final Gate。

## 已核实的外部能力

- PyInstaller 6.22.2 支持 `arm64`、`x86_64` 和 `universal2`，默认按当前运行架构生成单架构产物；官方明确说明不能把两个 PyInstaller 单架构 onefile 用 `lipo` 合成可用 universal2：[PyInstaller macOS multi-arch](https://pyinstaller.org/en/stable/feature-notes.html#macos-multi-arch-support)。
- PyInstaller 在 macOS 会重新签名被处理的 Mach-O；未提供身份时使用 ad-hoc 签名，指定真实 signing identity 时才会启用 hardened runtime：[PyInstaller macOS code signing](https://pyinstaller.org/en/stable/feature-notes.html#macos-binary-code-signing)。
- GitHub 当前标准 runner 标签中，`macos-15` 是 arm64，`macos-15-intel` 是 Intel x64：[GitHub-hosted runners](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)。
- `cloudflared 2026.7.3` 发布了 Darwin amd64/arm64 资产及 SHA-256：[Cloudflare release](https://github.com/cloudflare/cloudflared/releases/tag/2026.7.3)。
- Apple 的正式直接分发信任链仍依赖 Developer ID 与 notarization；本项目明确没有证书，因此本轮 candidate 不具备该信任链。Intel64 installer 已采用并真机验证自动 quarantine cleanup 作为 unsigned-candidate 可用性措施，但不得把它描述为 Apple trust/notarization：[Apple notarization](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution)。

## 合理推断与默认值

- 两个发布物应在对应原生 runner 上分别构建，而不是交叉构建、Rosetta 构建、universal2 后切片或 `lipo` 合并。
- 默认支持目标暂定为 macOS 13+；最终只能声明真实完成 clean-machine 验收的最低版本。
- 打包目录应保持只读、可移动；macOS 默认可写 home 使用 `~/Library/Application Support/codemcp-remote`，`--home` 与 `CODEMCP_HOME` 继续优先。
- 只把固定版本 `cloudflared` 打包为 macOS transport；不下载、不复制、不许可清点 `tunnel-client`，也不对其 PATH fallback 作发布承诺。
- macOS Keychain 通过条件依赖 `keyring>=25.7,<26; sys_platform == "darwin"` 的原生 macOS backend 接入。它避免把 secret 放进 `/usr/bin/security ... -w <secret>` 的 argv；Windows 继续使用现有 DPAPI，不改变已存 secret 格式。

## 已确认约束与待确认资源

- 用户已确认本轮没有 Developer ID Application 证书；两个精确命名的 tar.gz 因此是 ad-hoc 签名、未 notarized 的最终交付物。不得把它们描述为 Apple-trusted 或 notarized；installer 可自动清理 quarantine metadata，但这只是可用性措施，不改变签名/信任事实。
- Intel Mac 真机资源已经用于并完成 final unsigned-candidate gate；Apple Silicon 真机仍是剩余 Final Gate 资源。最低支持 macOS 版本只能按最终实际 host evidence 声明。
- GitHub native macOS runner 已完成 Phase 3 双架构 build/convergence gate；此前 billing/runner 阻塞已不再是当前发布阻塞项。

# Architecture Decision

| 决策点 | 选择 | 理由 |
| --- | --- | --- |
| 架构产物 | 两个原生 thin build：`arm64`、`x86_64` | 与用户要求一一对应；不依赖 Rosetta；PyInstaller 官方支持路径 |
| 冻结模式 | `onedir` + `--contents-directory .codemcp-runtime` | 保留单进程 executable 语义，避免 onefile self-spawn/临时目录问题；用户可见根目录稳定 |
| distribution/runtime 分离 | distribution 只读；macOS writable home 默认进入 Application Support | 解压目录可移动、可放只读位置，升级不会覆盖用户数据 |
| Secret storage | 环境变量优先；Windows DPAPI；macOS Keychain；其他平台无持久化 fallback | 保持兼容并 fail closed；不新增明文 secret 文件 |
| POSIX lifecycle | 后台进程 `start_new_session=True`；状态记录 PID、PGID 和稳定 start marker；停止前重新验证 | 安全清理完整进程组，拒绝 PID reuse 和不属于本实例的进程 |
| Transport | 两架构都只打包固定版本 `cloudflared` 到隐藏 runtime | 满足本轮 Cloudflare-only 范围，不依赖 Homebrew/PATH，也不扩大 OpenAI Tunnel 契约 |
| Interactive setup | 顶层 `codemcp-install.sh` 只负责编排正式 CLI，不自行拼接 TOML | 复用现有校验、受管配置写入与 secret backend，避免 shell escaping 形成第二套配置实现 |
| Supply chain | 仓库内 JSON manifest 固定 URL、文件名、版本、SHA-256；安全解包；记录 pre-sign/post-sign identity | 复用 Windows 发布原则，并处理 codesign 会改变 Mach-O hash 的事实 |
| Signing | 所有 macOS 产物使用 ad-hoc 签名；不提交 notarization | arm64 Mach-O 需要有效代码签名结构；用户明确没有 Developer ID 证书，必须诚实记录 Gatekeeper 限制 |
| Release scope | macOS 是 `v0.1.0` 的 additive target；Windows contract 不变 | 用户指定了 `v0.1.0` 文件名；共享运行时代码变化必须触发 Windows 回归 |

# Constraints

- 精确产物名必须是：
  - `codemcp-remote-v0.1.0-macos-arm64.tar.gz`
  - `codemcp-remote-v0.1.0-macos-intel64.tar.gz`
- 两个 archive 都只能有一个顶层 `codemcp-remote/`；除 `.codemcp-runtime/` 外不得新增未声明的顶层条目。
- `codemcp-install.sh` 是无 `sudo` 的首次配置向导，不复制二进制到系统目录；只允许写默认 user home、macOS Keychain 和明确注册的项目配置。
- 交互向导固定 `transport=cloudflare`、`auth.mode=none`、`network_trust.mode=cloudflare-chatgpt`；不得询问或生成 OpenAI Tunnel 配置。
- 向导不调用 Cloudflare API，也不创建 Tunnel、DNS、WAF/IP List；操作者必须先取得 remotely managed Tunnel token 和 public hostname，向导只生成本地配置。
- `arm64` 包内所有 Mach-O 必须含且只含 `arm64` slice；`intel64` 包必须含且只含 `x86_64` slice。
- 禁止在 Intel 产物中混入 Rosetta 构建结果，禁止用 `lipo` 合并或拆分 PyInstaller 产物。
- candidate build 必须来自 clean worktree 与精确 commit；Phase 2/3 允许 pre-tag candidate，若 HEAD 已存在 exact tag 则必须与版本一致。Final Gate signoff 后才创建 `v0.1.0` tag，并从最终 release commit 重新收敛发布证据；构建后不得修改跟踪文件。
- 安装运行时不得依赖 Python、`uv`、PowerShell、Homebrew 或源码仓库；Git 是唯一新增之外的明确工具前置条件。
- distribution 中不得出现运行期 `bridge.toml`、`projects.toml`、`remote.toml`、`tunnel.env`、Keychain 导出、token、API key、用户路径、日志、SQLite、PID/state 文件。
- `SHA256SUMS.txt` 必须按 POSIX 相对路径字节序排序，覆盖其自身之外的所有 regular files，包括隐藏 runtime 和 `BUILD_PROVENANCE.json`。
- `BUILD_PROVENANCE.json` 不得含 secret；至少记录 source commit/tag/dirty、target arch、runner OS/arch、Python/uv/PyInstaller、lock hash、每个外部输入、`signing.mode=adhoc`、无 Developer ID、`notarization.status=not_performed`、原因和生成时间。
- `THIRD_PARTY/` 至少保存 CPython、PyInstaller bootloader exception、codemcp、锁定 Python runtime dependencies 和 cloudflared 的 license/notice/SPDX 证据；macOS 新闭包必须重新做人工 license compatibility signoff。
- macOS Keychain 不可用、锁定或 backend 不匹配时必须明确失败；禁止自动退化为明文文件。
- 不改变 22-tool MCP schema、数据库 schema、operation/idempotency/checkpoint/CAS 契约，不增加任意 shell/argv/path 能力。
- Windows DPAPI、Windows distribution root 默认 home、Native Windows worker 和现有启动脚本行为必须保持兼容。
- 每个 Phase 完成后执行 `git diff --check`、`git status`、独立 commit，然后停止；不得自动进入下一 Phase。

# Impact Scope

| 范围 | 预计文件/模块 | 影响 |
| --- | --- | --- |
| 冻结入口与 home | `bridge/src/codemcp_bridge/main.py`、`bridge/tests/test_executable_entrypoint.py` | 增加平台感知的 packaged home；Windows 行为不变 |
| Secret abstraction | 新增 `bridge/src/codemcp_bridge/secret_store.py`，修改 `lifecycle.py`、`main.py` | DPAPI 与 macOS Keychain 统一来源/状态契约 |
| 生命周期 | `lifecycle.py`、`bridge/tests/test_phase3_lifecycle.py` | POSIX process group、marker、state v2、终止升级 |
| Transport discovery | `transports/base.py`、`cloudflare.py` 及对应测试 | 识别隐藏 bundled `cloudflared`，按平台/架构校验 |
| Python 依赖 | `bridge/pyproject.toml`、`bridge/uv.lock` | 仅 Darwin 条件新增 `keyring`；需依赖与 license 审计 |
| 构建工具 | 新增 `scripts/build-macos-release.sh`、`scripts/prepare-verified-release-asset.py`、`scripts/release-assets/macos-v0.1.0.json` | 双架构构建、供应链校验、assembly、provenance、tar |
| 用户脚本 | 新增 `scripts/codemcp-install.sh`、`scripts/codemcp-start.sh`、`scripts/codemcp-stop.sh` | relocatable POSIX 首次配置与生命周期入口 |
| 自动测试 | 新增 macOS packaging/host tests，扩展 lifecycle/transport tests | arch、layout、worker、Keychain、process ownership、负向测试 |
| CI/Release | `.github/workflows/ci.yml`、新增 `.github/workflows/macos-release.yml` | 原生 arm64/x86_64 job、ad-hoc 签名验证、artifact convergence |
| 文档/验收 | README、CHANGELOG、architecture/security/threat、guides、acceptance、open-source readiness | 只有验收后才把 macOS 从 planned 改为 supported |
| 数据库/API | 无 | 不做 schema migration，不改 MCP contract |

# Phases

## Phase 1: macOS 运行时与安全生命周期基线

### Goal

让现有源码运行时在 macOS 上具备正确的可写目录、Keychain secret、后台进程所有权和停止语义，同时保持 Windows 行为完全兼容。本阶段不构建 tar.gz。

### Files / Modules

- `bridge/src/codemcp_bridge/main.py`
- `bridge/src/codemcp_bridge/lifecycle.py`
- 新增 `bridge/src/codemcp_bridge/secret_store.py`
- `bridge/src/codemcp_bridge/transports/base.py`
- `bridge/src/codemcp_bridge/transports/cloudflare.py`
- `bridge/pyproject.toml`
- `bridge/uv.lock`
- `bridge/tests/test_executable_entrypoint.py`
- `bridge/tests/test_phase3_lifecycle.py`
- 对应 transport/settings tests

### Changes

1. 把 distribution root、bundled runtime root、writable home 分成三个明确概念：
   - distribution root：`codemcp-remote` 所在目录，提供模板与 notices；
   - bundled runtime root：`.codemcp-runtime`；
   - writable home：macOS packaged 默认 `~/Library/Application Support/codemcp-remote`。
2. 保留优先级：`--home` > `CODEMCP_HOME` > platform packaged default；Windows packaged default 仍为安装目录；source-mode POSIX 的历史 `~/.codemcp-remote` 不隐式迁移。
3. 新建小型 `SecretStore` 抽象：
   - environment 值始终最高优先级；
   - `WindowsDpapiSecretStore` 复用当前 `.dpapi` 文件与加解密函数；
   - `MacOSKeychainSecretStore` 显式实例化 `keyring.backends.macOS.Keyring`，service 固定为 codemcp-remote，account 由 canonical home hash + logical secret ID 组成；
   - unsupported/failed backend 只允许 environment-only，不写 plaintext fallback。
4. CLI `init` 与 `doctor` 返回真实 source：`environment`、`windows-dpapi`、`macos-keychain` 或 `none`，移除硬编码 Windows 成功文案。
5. POSIX `_popen_background` 使用新 session/process group；state 升级为 v2，记录 PID、PGID、opaque process start marker 和 executable identity。
6. Darwin marker 使用系统 `ps` 的稳定 start-time 输出作为 opaque 值，并固定 `LC_ALL=C`；每次 status/stop/TERM/KILL 前重新读取并比较。旧的 POSIX PID-only state 一律视为 `not_owned`，不杀进程；Windows v1 marker 保持兼容。
7. POSIX 停止先向已验证 PGID 发 `SIGTERM`，限时等待，再在二次 ownership check 后 `SIGKILL`；拒绝 PGID <= 1、当前进程组或 marker mismatch。
8. Cloudflare transport context 增加 bundled tool location，查找顺序固定为 bundled exact path -> distribution legacy path -> PATH；只有 bundled path 应用固定 hash/version 契约。OpenAI Tunnel provider 不在本阶段改动范围内。

### Dependencies

- 新增直接条件依赖：`keyring>=25.7,<26; sys_platform == "darwin"`，由 lockfile 固定到精确版本。
- 不新增 Windows 依赖；Keychain import 必须 lazy 且只在 Darwin 执行。
- Phase 2 依赖本阶段确定的 bundled runtime root 与 platform/arch 识别 API。

### Risks

- Keychain ACL 可能在 ad-hoc 二进制每次升级身份变化或用户 keychain 锁定时弹窗/拒绝访问；必须 fail closed，并在真实产物升级测试中确认用户体验与恢复路径。
- 错误的 process-group 终止可能杀到无关进程；ownership recheck、PGID guard 和负向测试是 P0。
- 改动 `lifecycle.py` 会影响 Windows release evidence；至少重跑完整自动回归，最终还需 Phase 4 Windows packaged regression。

### Validation

```text
uv lock --project bridge --check
uv sync --project bridge --frozen --all-groups
uv run --project bridge --frozen ruff check bridge/src bridge/tests tests/integration
uv run --project bridge --frozen ruff format --check bridge/src bridge/tests tests/integration
uv run --project bridge --frozen pytest -q bridge/tests/test_executable_entrypoint.py bridge/tests/test_phase3_lifecycle.py
uv run --project bridge --frozen pytest -q bridge/tests tests/integration
```

在 macOS 上另跑 Keychain round-trip（使用测试专用 service/account 并 finally 删除）和 process-group live smoke；在非 macOS CI 中使用 fake backend 测所有错误分支。

### Acceptance Criteria

- Windows packaged home 与 DPAPI 文件可读取性不变；现有 Windows unit/integration tests PASS。
- macOS packaged default home 精确为用户 Application Support；distribution 在只读目录仍可 `init`。
- Keychain round-trip 不产生 `.dpapi`、明文 secret 文件、argv secret 或 log secret。
- source labels 与真实 backend 一致，Keychain failure 返回明确错误。
- reused PID、marker mismatch、foreign PGID 均不会收到 signal；owned group 可 TERM/KILL 并无遗留子进程。
- MCP/DB/public CLI contract 除 source 文案与平台路径外不变。
- 阶段 commit 完成后停止，不进入 Phase 2。

## Phase 2: 双架构构建链与供应链产物

### Goal

完成可在原生 macOS arm64 与 Intel x86_64 环境执行的确定性冻结、签名、assembly、供应链 pin 与安装脚本实现，并用平台无关自动测试证明其安全契约。Phase 2 不再要求开发者本地拥有两种架构 Mac，也不以本地产出两个 candidate 作为 Gate；双架构权威 candidate 统一由 Phase 3 GitHub Actions 原生 runner 从同一 clean commit 生成。若开发者手头有原生 Mac，可额外运行对应单架构构建作为非阻塞 smoke。此阶段不宣称可公开发布。

### Files / Modules

- 新增 `scripts/build-macos-release.sh`
- 新增 `scripts/prepare-verified-release-asset.py`
- 新增 `scripts/release-assets/macos-v0.1.0.json`
- 新增 `scripts/codemcp-install.sh`
- 新增 `scripts/codemcp-start.sh`
- 新增 `scripts/codemcp-stop.sh`
- 复用 `scripts/windows_entrypoint.py`
- transport platform pin 常量及 tests
- 新增 `bridge/tests/test_macos_packaging.py`
- 新增/扩展 `tests/integration/test_macos_executable_host.py`
- `.gitignore`（仅在现有 `.local/` 规则不足时最小修改）

### Changes

1. 构建脚本第一步验证：Darwin、`uname -m` 与 requested arch 一致、Git clean、HEAD/version 一致、`uv`/Python 3.12/Xcode tools 可用；Phase 2/3 candidate 允许尚未创建 release tag，但若 HEAD 已存在 exact tag，则只能是 `v0.1.0`。最终 tag 仍由 Phase 4 Final Gate signoff 后创建；禁止 Rosetta/foreign arch。
2. 仓库 manifest 固定所有网络输入，下载到 `.local/third-party`，先写临时文件，校验 SHA-256 后原子替换；安全解包拒绝 absolute path、`..`、symlink/hardlink 和多余 executable candidate。
3. PyInstaller 构建工具闭包固定为：

   | 文件 | SHA-256 |
   | --- | --- |
   | `pyinstaller-6.22.2-py3-none-macosx_10_13_universal2.whl` | `ebd1b1ca932d7cf25d7366ce691aaf79a5ff9425811ed7328b5116e4471b6d6d` |
   | `pyinstaller_hooks_contrib-2026.6-py3-none-any.whl` | `fd13b8ac126b35361175edacd41a0d97080b75dd5f4b594ecefefff969509dd3` |
   | `altgraph-0.17.5-py2.py3-none-any.whl` | `f3a22400bce1b0c701683820ac4f3b159cd301acab067c51c653e06961600597` |
   | `macholib-1.16.4-py2.py3-none-any.whl` | `da1a3fa8266e30f0ce7e97c6a54eefaae8edd1e5f86f3eb8b95457cae90265ea` |
   | `packaging-26.3-py3-none-any.whl` | `d7193f7c8e4e93f444fde0262bf90af30e16fa0ad0ad44cb553c87339b23cd1c` |
   | `setuptools-84.0.0-py3-none-any.whl` | `51a52592b3b99e102b609654876bd65f19f999935166d1352678931132b0c670` |

4. transport 输入只固定：

   | 架构 | 资产 | 当前 GitHub release asset SHA-256 | upstream release-notes SHA-256 |
   | --- | --- | --- | --- |
   | x86_64 | `cloudflared-darwin-amd64.tgz` 2026.7.3 | `70d1c8684fa6d14b5843787ec8d1ea8e18b23650e424f4ea43d849a506487c3b` | `e88fe5874d42a94f49a7ea59cabc3722d2962d0449232b0f3b1a426a712e275c` |
   | arm64 | `cloudflared-darwin-arm64.tgz` 2026.7.3 | `90c5a4f914d705fd70c135dba6d80b1791d254b08d6d4136301941f88330dd09` | `f35c50089cd25f77a4cb5a2152036bc26db15aa31fbe11f7995d2e42a4ed6257` |

   2026-08-30 复核发现 Cloudflare 2026.7.3 release notes 中的 Darwin checksum 与 GitHub 当前 release asset digest 不一致。GitHub 将 `ReleaseAsset.digest` 定义为资产内容的 SHA-256，因此构建下载 pin 使用当前 GitHub asset digest；release-notes checksum 作为上游不一致证据保留在 manifest/provenance 中，不用于放行当前下载。任何后续 asset digest 变化必须 fail closed，并更新 manifest、runtime pins、tests 和 license review。同步保存 commit-pinned cloudflared license 证据；manifest 中不得出现 `tunnel-client` 输入。
5. PyInstaller 使用 `--onedir --console --target-arch <arm64|x86_64> --contents-directory .codemcp-runtime`，显式收集 `codemcp` metadata/submodules 与 macOS Keychain backend；不使用 UPX。
6. 把 verified `cloudflared` 放入 `.codemcp-runtime/bin/`，运行时优先使用它。先记录 upstream/extracted hash，再执行 ad-hoc signing，最后记录 installed hash 与 `Signature=adhoc` 证据；不得查找 Developer ID identity 或提交 notarization。
7. assembly 只复制：主 binary、三个 `.sh`、两个现有 config template、根 LICENSE、完整 `THIRD_PARTY/`、provenance 和 checksum；不复制任何本地 runtime 配置或状态。
8. `codemcp-install.sh` 定义为无特权、Cloudflare-only 的首次配置向导：
   - 启动时要求 stdin/stdout 是 TTY，定位自身 distribution，并在读取 secret 前自动验证内部 `SHA256SUMS.txt`；外部 archive digest 仍必须由用户先按指南核对；
   - 首先说明 Cloudflare Tunnel、DNS 与 WAF/IP List 是外部前置条件，向导不会请求 Cloudflare API token 或自动修改账号资源；
   - 显示固定的 packaged writable home `~/Library/Application Support/codemcp-remote`，逐项询问 Cloudflare public MCP URL、精确 allowed host、可选 allowed origin（默认 `https://chatgpt.com`）和是否注册首个 Git 项目；向导不提供 custom home，advanced 用户改用 `CODEMCP_HOME` + 非交互 CLI；
   - 通过关闭 terminal echo 读取必填 Tunnel token，并用 `trap` 在正常退出、EOF、INT、TERM 时恢复 terminal、清空 shell 变量和 `unset TUNNEL_TOKEN`；不得把 token 放入 argv、临时文件、TOML、输出或日志；
   - 展示不含 secret 的确认摘要；所有值始终作为单独、完整引用的 argv 传给 `codemcp-remote init --transport cloudflare --auth-mode none --network-trust cloudflare-chatgpt --store-transport-secret`，可选 origin 为空时省略对应 flag，不用 `eval`，也不在 shell 中自行拼 TOML；
   - 由正式 CLI 在 `<home>/config/` 生成 `bridge.toml`、`projects.toml`、`remote.toml`、不含 secret 的 `tunnel.env`，并把 token 写入 macOS Keychain；可选项目通过 `codemcp-remote project add` 注册；
   - 若 home 已初始化则不带 `--force` 覆盖，显示 `doctor/configure/project` 的后续路径；失败时不得自动启动服务，必须返回非零并给出可安全重试的状态；成功后运行 `doctor`，但不自动建立公网连接。
9. `codemcp-start.sh` / `codemcp-stop.sh` 使用 POSIX `sh`、从脚本自身解析绝对 distribution path、全程引用变量、不用 `eval`、不注入 `--home`，并保留退出码。
10. 规范权限：目录 `0755`，binary/安装启动停止脚本/`cloudflared` `0755`，配置/许可证/JSON/checksum `0644`；剥离构建机遗留的 quarantine、Finder metadata、resource fork、`.DS_Store`、`__MACOSX`。
11. `BUILD_PROVENANCE.json` 使用 `codemcp-remote-build-provenance-v2`，明确记录 ad-hoc/no-certificate/not-notarized；生成后再生成 `SHA256SUMS.txt`；tar 必须只有一个顶层目录且保留执行位。

### Dependencies

- 依赖 Phase 1 的 runtime root、Keychain 和 process lifecycle contract。
- Phase 2 实现与平台无关测试不要求开发者同时拥有 Intel 与 Apple Silicon Mac；只有执行可选 native smoke 时才需要对应原生 macOS、Xcode Command Line Tools、Python 3.12 和 `uv`。
- 双架构 candidate 的权威构建、ad-hoc 签名验证与产物收敛统一在 Phase 3 GitHub-hosted 原生 macOS runners 完成。

### Risks

- PyInstaller hidden imports、`cryptography`、`pydantic-core`、`rpds-py`、`ruff` 等 Mach-O slice 不完整会导致构建或运行失败；必须逐文件架构扫描。
- 对 `cloudflared` 做 ad-hoc 重签名会改变 installed hash；provenance 必须同时保存 upstream、pre-sign 和 post-sign 证据，不能拿 upstream hash 错验已签名文件。
- tar 元数据可能泄漏本机用户名、路径、xattr 或造成非确定性；assembly 必须在干净 staging root 中完成并检查 archive listing。
- 交互脚本若错误处理 quoting、signal 或 terminal echo，可能形成命令注入或泄漏 Tunnel token；所有 prompt 分支必须在真实 pseudo-TTY 中做恶意输入、EOF/INT/TERM 和 secret-canary 测试。
- `init` 在 Keychain 或后续配置失败时可能留下部分非 secret 文件；向导不得 `--force` 覆盖既有 home，必须检测已有/部分状态、保持 fail closed 并给出明确的修复或安全重试路径。

### Validation

Phase 2 的必需 Gate 以平台无关的实现与安全测试为准：

```text
uv lock --project bridge --check
uv sync --project bridge --frozen --all-groups --python 3.12
uv run --project bridge --frozen ruff check bridge/src bridge/tests tests/integration scripts/dependency_license_audit.py scripts/prepare-verified-release-asset.py
uv run --project bridge --frozen ruff format --check bridge/src bridge/tests tests/integration scripts/dependency_license_audit.py scripts/prepare-verified-release-asset.py
uv run --project bridge --frozen pytest -q bridge/tests/test_macos_packaging.py bridge/tests/test_phase1_macos_runtime_security.py
uv run --project bridge --frozen pytest -q bridge/tests tests/integration
uv build --project bridge
git diff --check
git status --short
```

同时直接执行 manifest `--check`，证明 URL/version/SHA-256/license pin 完整，恶意 archive 与 digest drift 会 fail closed；shell/static tests 必须覆盖非 TTY、existing-home、secret 不进入 argv/TOML/log、无 `eval`、bundled `cloudflared` 优先级与 provenance schema。

如果当前开发者恰好拥有某一种原生 Mac，可额外执行该架构的非阻塞 smoke：

```text
./scripts/build-macos-release.sh --version 0.1.0 --arch <native-arch> --mode candidate
```

该 smoke 可提前发现 PyInstaller/Mach-O/Keychain 问题，但 **不作为 Phase 2 完成条件，也不构成另一架构或正式发布证据**。两个架构的权威 candidate、Mach-O slice/signature、tar layout/checksum 与 frozen worker smoke 在 Phase 3 原生 CI 中统一验证。

### Acceptance Criteria

- 构建器、verified asset helper、manifest、三个用户 shell 脚本及对应 tests 全部落地，且供应链输入固定并 fail closed。
- 构建脚本明确要求 Darwin 与 requested arch 匹配；不提供 Windows/Linux 交叉构建、Rosetta 伪构建、`lipo` 合并/切片路径。
- PyInstaller contract 固定为 `onedir` + `.codemcp-runtime`，bundled `cloudflared`、ad-hoc signing、provenance、内部 checksum、license evidence 与 deterministic tar assembly 都有自动 contract test。
- 安装向导只编排正式 CLI；非 TTY、existing/partial home、secret handling、quoting 与取消路径有平台无关测试，且不存在 plaintext fallback、`eval` 或 secret argv。
- shared runtime/transport 改动没有引入新的自动回归；若主线存在预先记录的基线失败，Phase 2 只允许保持同一 failure set，不混入无关修复。
- Phase 2 不要求本地产出 arm64 与 Intel 两个 tar；正式双架构 candidate 必须由 Phase 3 同一 source commit 的两个 GitHub-hosted 原生 macOS jobs 生成。
- dependency/license inventory 与 secret audit 无新增 blocker；阶段 commit 后停止，不进入 Phase 3。

## Phase 3: 原生 CI、ad-hoc 签名与发布收敛

### Goal

由 GitHub Actions 作为双架构 candidate 的权威构建来源：从同一 clean source commit 分别在原生 Apple Silicon 与 Intel GitHub-hosted macOS runner 上生成、验证并收敛两个 ad-hoc 签名且明确未 notarized 的发布候选；仍不自动创建 stable GitHub Release。开发者本地 Mac 不承担权威发布构建职责。

### Files / Modules

- `.github/workflows/ci.yml`
- 新增 `.github/workflows/macos-release.yml`
- `scripts/build-macos-release.sh`
- 新增 `scripts/validate-macos-release.sh`
- macOS integration/packaging tests
- `docs/acceptance/macos-v0.1.0-validation.md`（初始 ledger）

### Changes

1. core CI 增加至少一个 macOS arm64 job，验证 source-mode runtime、Keychain fake/real-safe smoke 和 POSIX lifecycle；普通 PR 和 release workflow 均不配置不存在的 signing/notary secret。
2. release workflow 使用两个明确 matrix entry：
   - `macos-15` + `arm64` + `macos-arm64`；
   - `macos-15-intel` + `x86_64` + `macos-intel64`。
3. actions 全部 pin 到 commit，checkout 不持久化 credentials；权限默认 `contents: read`，只有启用 artifact attestation 时增加最小 `id-token: write`。
4. 每个原生 job 都执行相同的 deterministic assembly，并对主程序、PyInstaller 收集的 Mach-O 与后置 `cloudflared` 做 ad-hoc 签名；不得导入临时 keychain、Developer ID 或 notary 凭据。
5. 逐个 Mach-O 执行严格 `codesign` 验证，断言不存在项目 Developer ID Team ID；provenance 明确记录 `adhoc`、`developer_id=false`、`not_performed` 及 `no_certificate` 原因。
6. workflow 不生成或提交 notarization 容器，不调用 `notarytool`，也不把 `spctl` 拒绝掩盖成成功。
7. 最终 convergence job 下载两个 workflow artifacts，验证 source commit/version/schema/tool versions/signing mode 相同，target arch/name 不同，重新计算 tar SHA-256，并保存不可变 evidence。
8. workflow 只上传 candidate、外部 archive digest 和 evidence；是否发布/tag 由 Phase 4 Final Gate 决定。

### Dependencies

- Phase 2 构建链、供应链 manifest、packaging/security contract tests 与 shared regression Gate 已完成；不要求 Phase 2 本地产出两个 candidate。
- 需要可用 GitHub-hosted macOS runners：Apple Silicon job 与 Intel x86_64 job；不需要 Apple Developer Program、Developer ID certificate 或 notary credentials。
- 对公开仓库优先使用 GitHub standard hosted runners；若 hosted runner 因平台/billing/availability 不可用，可使用受控的原生 self-hosted Mac，但必须记录 runner identity、工具版本与 waiver，且不能把 self-hosted 结果表述为 GitHub-hosted PASS。

### Risks

- 带 quarantine 的互联网下载通常不会无提示通过 Gatekeeper；这是“无证书”的已接受产品限制，不得通过修改测试期望或删除下载证据来隐藏。
- GitHub arm64 runner 能提供原生 Apple Silicon 构建证据，但不能替代 Phase 4 的真实 Apple Silicon clean-machine 用户环境验收。
- ad-hoc identity 不提供开发者来源认证；供应链信任必须依赖可信发布渠道中的 archive SHA-256、内部逐文件 checksum 和 provenance。

### Validation

- workflow 两个 build job PASS，并报告 `runner.os`、`runner.arch`、`uname -m` 与 requested arch 一致。
- `codesign --verify --deep --strict --all-architectures` 对全部 Mach-O PASS，详细信息显示 ad-hoc 而非项目 Developer ID。
- `spctl --assess --type execute --verbose=4` 在模拟 quarantine 的主 binary 上预期非零并记录拒绝原因；该负向结果是限制证据，不是构建失败。
- 断言日志中没有 `notarytool submit`，provenance 中没有伪造的 submission ID、Team ID 或 notarized 状态。
- 重解 workflow artifact 后重新跑 Phase 2 layout/arch/checksum/worker/Cloudflare smoke。
- 确认 workflow 日志和上传 artifacts 不含 certificate、password、API key、Keychain 文件或 secret 环境值。

### Acceptance Criteria

- 两个 candidate 绑定同一个 clean source commit 和版本；provenance target arch 分别正确。
- 全部 Mach-O 的代码签名结构可验证，项目自有代码为 ad-hoc；provenance 明确 `notarization.status=not_performed`。
- 候选 tar 在重新下载后 checksum、layout、执行位、arch 与 build job 一致。
- CI 不使用 Rosetta 构建 Intel 包，不在一个 runner 上伪造另一架构产物。
- 文档和 artifact metadata 均未声称 Developer ID、notarized 或默认 Gatekeeper trust。
- 阶段 commit 后停止，不进入 Phase 4。

## Phase 4: Clean-machine、文档一致性与最终发布门禁

**当前状态：INTEL64 LIVE GATE PASS / ARM64 REAL-HOST ACCEPTANCE PENDING / RELEASE BLOCKED**

### 2026-08-31 仓库真实状态

以下状态以仓库为准，不以聊天记录为准：

- 已实现 `scripts/validate-clean-macos-release.sh`，包含 `prepare`、`verify`、`secret-scan`、`lifecycle`、`cleanup` 五个动作；macOS build/install 指南与 packaging contract tests 已覆盖相应流程和关键安全不变量。
- `BridgeError` dataclass 构造器中的 zero-argument `super()` 问题已改为显式 `Exception.__init__`；部署后的 Intel64 runtime 已不再出现原来的 `super(type, obj)` 原始异常。
- 已为 effectful MCP tools 发布标准 `ToolAnnotations`。真实 ChatGPT → codemacos 链路已验证 `checkpoint_create`、`approval_confirm`、`checkpoint_restore`、`registered_command_run`、`test_run`、`format_run` 可以正常到达 Bridge，同时 Bridge 自身 approval、CAS、checkpoint、audit、fail-closed 仍保持权威。
- Intel64 真机已经完成 project open/read/write、自动 Git commit/checkpoint/diff、一次性 approval、防重放、checkpoint restore、stale expected-head CAS 拒绝、project registry hot reload，以及固定 `verify/test/format` registered command 验收。
- `scripts/codemcp-install.sh` 现在在 Darwin 上解析 `SCRIPT_DIR` 后，对当前解压目录递归执行 `xattr -dr com.apple.quarantine "$SCRIPT_DIR" 2>/dev/null || true`，再继续原有安装流程。该行为只作为无 Developer ID / 未 notarized candidate 的可用性措施，不修改 packaged file bytes，也不等价于 Apple trust/notarization。
- 最新 Intel64 fresh candidate 已完成真实 quarantine 验收：未手工运行任何 `xattr -dr`，直接执行 `./codemcp-install.sh` 即可进入正常安装流程，之前 bundled Python extension 的 Gatekeeper/quarantine 阻断不再出现。**quarantine PASS**。
- 全量仓库回归最终结果为 **393 passed, 0 failed, 8 skipped, 1 warning**。此前剩余的两个 real-codemcp integration failure 已确认来自过时 Phase 2 fixture 强制 `worker_mode="wsl2"`，而当前发布契约已经是 `adapter_mode="native-stdio"` + `worker_mode="local"`；测试基线对齐当前发布路径后全绿。
- Intel64 无证书 candidate 的 final release gate 已 PASS。
- Apple Silicon 真机 clean-host / final gate 仍待执行；GitHub arm64 native candidate 不能替代真实 Apple Silicon host evidence。
- 因此 macOS 双架构与同一 `v0.1.0` 的最终 Release Gate 仍保持 BLOCKED，目前主要剩余门禁是 ARM64 真机证据及依赖该证据的最终文档/release signoff。

### Goal

在真实 Apple Silicon 与 Intel Mac 上分别验证由 Phase 3 CI 产生的两个 ad-hoc 签名、未 notarized tar 的下载校验、installer quarantine 处理、运行、安全、升级与清理行为；Intel64 已完成 final unsigned-candidate gate，Apple Silicon 仍待同等真机证据。真机负责用户环境验收，不重新定义权威构建来源。同步现行文档，并把结论并入 `v0.1.0` Final Release Gate。

### Files / Modules

- `docs/acceptance/macos-v0.1.0-validation.md`
- 新增 `docs/guides/macos-build-install-use.md`
- `README.md`
- `CHANGELOG.md`
- `docs/README.md`
- `docs/architecture/architecture.md`
- `docs/architecture/security-model.md`
- `docs/architecture/threat-model.md`
- `docs/guides/operations-runbook.md`
- `docs/acceptance/acceptance-test-plan.md`
- `docs/plans/v0.1.0/open-source-readiness-plan.md`
- 必要的 clean-machine harness 与 tests

### Changes

1. 建立 macOS acceptance ledger，所有结果绑定 source commit、archive SHA-256、hardware、OS build、arch、`Signature=adhoc` 和 `notarization.status=not_performed`。
2. 在真实下载/quarantine 路径解压后，运行任何 extracted code 前先对照可信发布渠道核验 archive SHA-256；内部 `SHA256SUMS.txt` 继续作为 distribution integrity gate。`./codemcp-install.sh` 在 Darwin 上会 best-effort 递归清除自身解压目录的 `com.apple.quarantine` 后继续正式 CLI 配置流程，因此最终 unsigned-candidate 验收要求“不再需要额外手工 `xattr -dr`”。该自动清理不修改文件 bytes，也不能被描述为 Developer ID、notarization 或 Apple trust。
3. 使用 disposable Git repo 完成 frozen worker read/mutation/replay/checkpoint/restore，验证最终 branch/HEAD/worktree 精确恢复。
4. 两架构各跑至少 20 次 start/status/stop；覆盖 Bridge/Tunnel/worker crash、端口占用、stale/reused PID、TERM/KILL、断连/unknown/reconcile 和无遗留进程。
5. 验证 Keychain store/read、删除 shell 中 plaintext secret 后重启、distribution 目录移动/升级后继续读取同一 home 的 secret；验证 keychain locked/denied 时 fail closed。
6. 隔离运行 PATH，证明 Python/`uv`/PowerShell/Homebrew 不可见，packed `cloudflared` 被使用；Git 缺失时 doctor 给出明确失败，PATH 中存在 `tunnel-client` 也不得成为 macOS 验收依赖。
7. 覆盖空格、中文路径、大小写不敏感 APFS、symlink escape、LF、长路径与 distribution read-only。
8. 共享 runtime 改动触发完整 Windows suite 和 packaged Windows smoke；只有确证没有回归后才能更新跨平台文档。
9. 文档从 Windows-only 调整为 platform matrix，分别描述 Windows DPAPI、macOS Keychain、Windows installer、macOS tar、交互向导的逐项提示/生成文件/取消与恢复路径、各自 prerequisites 与已验证 OS；历史 release/report 不改写，只链接新 evidence。
10. 更新 open-source readiness：macOS 不再是 P2，两个 mac artifacts 成为同一 `v0.1.0` tag 的 gate；任何平台未通过都保持 stable release blocked，并显式记录无证书/无 notarization 的发布例外。

### Dependencies

- Phase 3 GitHub Actions 从同一 clean source commit 生成并收敛的 arm64 与 Intel ad-hoc signed、未 notarized candidates。
- 至少一台真实 Apple Silicon Mac、一台真实 Intel Mac；不要求特定 M1/M2/M3/M4 代际。最低支持 macOS 版本必须有真实 host/VM 证据，最终声明只覆盖实际验证的 hardware/OS 范围。
- Windows 回归需要 Windows 11 x64 host。
- 真正 remote Connector 验收需要受控 Cloudflare 配置，但不得把凭据写入仓库或公开 evidence。

### Risks

- Intel64 真机资源已完成 final gate；当前主要资源风险是 Apple Silicon 真机与最低支持 macOS 版本证据不足，不能用 CI arm runner、Rosetta 或另一架构证据代替真实 ARM64 release claim。
- macOS quarantine、Keychain ACL、真实 terminal signal/echo 和企业安全软件行为无法由 unit test 完全覆盖。
- 无 Developer ID/notarization 会降低来源信任等级；指南必须要求在运行 extracted code 前核验可信外部 archive digest，并明确 installer 自动 quarantine cleanup 只是可用性措施，不是签名/信任替代。
- 把 macOS 加入 `v0.1.0` 会延长当前 release；若业务决定不让它阻塞 Windows v0.1.0，必须另行明确版本/experimental contract，而不是在本计划内默认拆分。

### Validation

最终同一 commit 至少执行：

```text
uv sync --project bridge --frozen --all-groups
uv run --project bridge --frozen ruff check bridge/src bridge/tests tests/integration scripts/dependency_license_audit.py scripts/prepare-verified-release-asset.py
uv run --project bridge --frozen ruff format --check bridge/src bridge/tests tests/integration scripts/dependency_license_audit.py scripts/prepare-verified-release-asset.py
uv run --project bridge --frozen pytest -q bridge/tests tests/integration
uv build --project bridge
git diff --check
git status --short
```

另外执行两套 macOS clean-machine harness、Windows packaged regression、tracked tree/history/final artifact secret scan、dependency vulnerability audit、license inventory/manual signoff、22-tool contract 和最终 release acceptance。

### Acceptance Criteria

- 真实 Apple Silicon 与 Intel clean host 都能从 Phase 3 CI tar 运行交互向导，逐项输入后自动生成正确用户态配置，再完成诊断、受控 mutation、恢复与停止，无手工 TOML 或开发工具隐式依赖。
- 无 Developer ID / 未 notarized 的限制与文档一致；外部 archive digest 必须在运行 extracted code 前核验，内部 checksum 继续 PASS。Intel64 已验证 installer 自动 quarantine cleanup 后无需额外手工 `xattr -dr` 即可继续运行；ARM64 必须重复同等真机验收。
- Keychain 与 process ownership 的正常、升级、拒绝、崩溃分支全部有证据；没有 secret/log/process 残留。
- 两架构 20/20 lifecycle PASS，关键安全/可靠性 matrix PASS，无未解释 skip。
- Windows 自动/packaged 回归 PASS；22-tool/MCP/DB/security contract 未变化。
- README、architecture、security/threat、operations、acceptance、CHANGELOG 和 open-source readiness 与真实支持矩阵一致。
- 两个最终 tar 从同一个 release commit 重建，archive SHA-256 发布并与下载内容一致。
- Final Gate signoff 后才允许 tag/publish `v0.1.0`；阶段 commit 后停止。

# Final Validation

最终证据必须回答并通过以下检查：

1. **Source identity**：tag、commit、branch、dirty=false、`bridge/uv.lock` hash 一致。
2. **Architecture**：主 binary、Python dylib、扩展模块和 cloudflared 全部是目标 thin arch；payload 不含 `tunnel-client`。
3. **Artifact shape**：单一顶层目录、稳定可见条目、唯一隐藏 runtime、无 unsafe archive entry、权限正确。
4. **Integrity**：内部 `SHA256SUMS.txt` 全 PASS；release 页面记录两个 tar 的外部 SHA-256；provenance 可审计。
5. **Signing / quarantine**：全部 Mach-O 的 ad-hoc 签名结构 PASS；Developer ID 缺失、notarization 未执行与 provenance/指南一致；可信 archive digest 在运行 extracted code 前完成核验，Intel64 installer 自动 quarantine cleanup 已真机 PASS，ARM64 需完成同等证据。
6. **Interactive setup**：`codemcp-install.sh` 在两架构真实 TTY 上完成 Cloudflare-only 配置、Keychain 写入、可选项目注册、取消/失败恢复和 existing-home 保护；没有 shell injection 或 secret 泄漏。
7. **Runtime independence**：isolated PATH 下 Python/uv/pwsh/Homebrew 不可见；Git 和 bundled cloudflared 行为明确。
8. **Lifecycle/security**：Keychain、PID reuse、process group、crash、timeout、unknown/reconcile、log redaction、loopback/network trust PASS。
9. **Functional contract**：frozen worker、22 tools、project authorization、mutation/replay/approval/checkpoint/CAS restore PASS。
10. **Cross-platform regression**：Windows DPAPI/package/lifecycle 与 Ubuntu source CI PASS。
11. **Supply chain/legal**：所有新增输入已 pin、扫描、归档 license/SPDX；macOS exact payload 完成人工 compatibility signoff，并提供 AGPL Corresponding Source。

任何运行时代码、lockfile、外部二进制、签名流程或 artifact assembly 的后续变化，都会使受影响的平台证据失效并要求重跑。

# Open Risks

| 风险 | 当前状态 | 处理规则 |
| --- | --- | --- |
| 无 Developer ID / notarization | 用户已确认并接受的限制 | 只发布 ad-hoc、未 notarized tar；不得声称 Apple trust；可信 archive digest 必须在运行 extracted code 前核验 |
| GitHub hosted native build | Phase 3 双架构 native gate PASS | 保留同源 commit/provenance/convergence 证据；CI arm64 不能替代 Apple Silicon 真机验收 |
| 最低 macOS 版本未由用户指定 | 待完整双架构证据 | 只声明真实 clean-host 验证过的最低版本；不能仅靠 deployment target 推断 |
| 用户给出的树未列隐藏 runtime | 已选 `.codemcp-runtime/` 并进入现行 packaging contract | 若未来要求零隐藏条目，需另立 onefile/lifecycle ADR，不在 v0.1.0 内静默改变 |
| Keychain ACL/升级提示 | Intel64 基础真实链路已通过；完整跨升级/ARM64 matrix 仍待补 | ad-hoc binary 升级、distribution relocation 与 Apple Silicon 路径继续 fail closed 验收 |
| Intel 与 Apple Silicon 真机资源 | Intel64 final unsigned-candidate gate PASS；Apple Silicon 待补 | 缺 ARM64 真实 clean-host 验收时，不完成 macOS 双架构最终支持声明，也不完成同一 v0.1.0 Final Gate |
| tar 无 notarization ticket/staple | 无证书范围内的已知限制；Intel64 installer quarantine auto-cleanup PASS | 自动清 quarantine 只是 usability measure，不等价于 Apple trust；若未来要求正式免提示分发，另立 Developer ID/notarization/`.pkg` 或 `.dmg` 范围 |
| 交互向导输入/中断/重复运行 | Intel64 真实安装路径已通过；ARM64/剩余边界 matrix 待补 | 正式 CLI 负责解析与写配置；未覆盖边界仍按 release gate 继续验证 |
| `codemcp==0.3.0` macOS 兼容性 | Intel64 真实项目 read/write/checkpoint/restore/commands PASS；ARM64 待证据 | ARM64 frozen mutation smoke 与真实项目验收仍是硬门禁；失败时不得静默替换 backend |
| 新增 keyring/license closure | 自动 dependency/license inventory 与 secret audit 已通过；最终双架构 exact-payload signoff 随 ARM64 gate 收口 | lock、artifact license inventory、人工 compatibility signoff 完整后才可发布 |
| v0.1.0 从 Windows-only 扩大到 macOS | 明确 scope change | 两平台共享代码回归与文档门禁必须更新；旧 Windows RC 不能证明新 tag 全部通过 |

# Developer Handoff

不要重做已经完成的 Intel64 工作。当前仓库与真实 Intel Mac 已经完成 unsigned-candidate final gate。

Phase 4 从以下精确位置继续：

1. 保持当前全量回归绿色基线：**393 passed, 0 failed, 8 skipped, 1 warning**；
2. 保留 Intel64 已完成的 approval/restore/CAS、registered commands、project-registry hot reload、quarantine auto-cleanup 与 final live gate 证据；
3. 保持同一 release contract 下 arm64 candidate 的 CI/native packaging evidence 为绿色；
4. 在真实 Apple Silicon Mac 上执行等价 clean-host evidence chain，至少覆盖 installer/quarantine、Keychain、read/write/checkpoint/restore、registered commands、lifecycle/security 与 cleanup；
5. 将 ARM64 结果同步写入 acceptance ledger、英文 canonical `docs/implementation-plan.md`、本独立中文计划以及 `docs/development-state.md`；
6. 只有 ARM64 真机 final gate 与依赖该证据的最终 documentation/release signoff 全部完成后，才能把 macOS 双架构与同一 `v0.1.0` Release Gate 标记为 PASS。

旧 `BridgeError.__post_init__` 42-failure 基线以及后续两个 WSL2 real-codemcp failure 均已关闭，不得继续作为 accepted defect 带入后续阶段。后续任何 runtime、packaging、signing、external binary、installer 或 lockfile 变化，只要会影响已有证据，就必须重跑对应 gate。

**项目规则：关键阶段状态、PASS/FAIL、阻塞项、待补证据和下一步入口必须同步写入 `docs/development-state.md`、英文 canonical、独立中文实施计划以及相关 acceptance 文档；聊天记录不具备权威性。**
