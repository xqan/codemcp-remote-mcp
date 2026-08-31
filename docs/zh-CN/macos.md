# macOS 当前状态与发布限制

英文 canonical：

- [macOS build/install/use](../guides/macos-build-install-use.md)
- [macOS validation ledger](../acceptance/macos-v0.1.0-validation.md)
- [Implementation plan](../implementation-plan.md)

## 当前状态

macOS 是 `v0.1.0` 的增量支持轨道，但当前仍不能声明正式 supported。

已完成：

- native `arm64` GitHub-hosted candidate build gate：PASS；
- native `x86_64` GitHub-hosted candidate build gate：PASS；
- candidate convergence gate：PASS；
- runtime、packaging、Keychain、POSIX lifecycle 的主要实现已落地。

仍需完成：

- 真实 Intel Mac Phase 4 clean-host；
- 真实 Apple Silicon Mac Phase 4 clean-host；
- 两架构最终 20/20 lifecycle；
- Keychain upgrade/relocation/locked/denied matrix；
- 真实 quarantine -> integrity verification -> manual release 用户路径；
- 受共享代码影响的 Windows packaged regression；
- final support/documentation gate。

## 发布物

目标文件：

```text
codemcp-remote-v0.1.0-macos-arm64.tar.gz
codemcp-remote-v0.1.0-macos-intel64.tar.gz
```

解压后主目录：

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
├── BUILD_PROVENANCE.json
└── .codemcp-runtime/
```

## 签名和 Gatekeeper

当前没有 Developer ID Application certificate，因此：

- 使用 ad-hoc signing；
- 不做 notarization；
- 不能声称 Apple-trusted；
- 互联网下载带 quarantine 时，Gatekeeper 默认拒绝属于预期行为。

安全顺序必须是：

1. 从可信发布渠道取得 archive SHA-256；
2. 验证下载的 `.tar.gz`；
3. 解压；
4. 验证内部 `SHA256SUMS.txt`；
5. 确认 provenance/signing 信息；
6. **之后**才由用户显式解除 quarantine；
7. 再执行安装向导。

不能为了“看起来能运行”而让脚本自动删除 quarantine。

## Secret

macOS 使用 Keychain，而不是明文文件。

环境变量仍然优先；持久 secret backend 不可用、被锁或拒绝时必须明确失败，禁止自动降级成 plaintext。

## 交互安装

最终用户入口是：

```bash
./codemcp-install.sh
```

向导只配置 Cloudflare 推荐路径，不负责创建 Cloudflare Tunnel、DNS、WAF 或 IP List。

它应逐项收集：

- public MCP URL；
- exact allowed host；
- optional allowed origin；
- Tunnel token（隐藏输入）；
- optional first Git project。

Tunnel token 不进入 argv、TOML、日志或临时文件。

## 当前结论

GitHub native build PASS 只能证明 candidate 的构建和收敛，**不能替代真实 Mac 用户环境验收**。

在 Phase 4 证据闭环前，文档应使用：

```text
macOS candidate / validation in progress
```

而不是：

```text
macOS supported
```
