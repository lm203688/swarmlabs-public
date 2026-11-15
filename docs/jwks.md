# SwarmLabs JWKS 端点（Ed25519 签名验证）

**目标**：让第三方 agent 通过标准 `.well-known/jwks.json` 端点验证 SwarmLabs 发布的
`.well-known/mcp.json` / `.well-known/agent-card.json` 是否由 SwarmLabs 官方私钥签发，
对齐 AIsa Agent-Readable Web 叙事（Agent 在自动发现 API 前先做完整性校验）。

**发布**：
- JWKS 公开端点：`https://swarmlabs.tools/.well-known/jwks.json`
- 私钥：**本地保管，不入 git**（`.gitignore` 已排除 `.well-known/keys/`）
- 签名密钥：`swarmlabs-tools-2026-primary`（2026-09-22 首版）

---

## 一、密钥生命周期

### 1.1 生成（首次发布）

```bash
python scripts/jwks/jwks_tool.py generate-keys --kid "swarmlabs-tools-2026-primary"
```

产出：
- `.well-known/keys/swarmlabs-tools-2026-primary.key` —— Ed25519 私钥（32 bytes，本地）
- `.well-known/keys/swarmlabs-tools-2026-primary.pub` —— Ed25519 公钥备份
- `.well-known/jwks.json` —— 公开端点（含公钥 `x` 字段）

### 1.2 签名 manifest

```bash
python scripts/jwks/jwks_tool.py sign .well-known/mcp.json \
    --key .well-known/keys/swarmlabs-tools-2026-primary.key --apply
```

签名流程：
1. 读取 manifest，剔除 `signature` 字段本身；
2. 按 `sort_keys=True, separators=(",", ":")` 生成规范化 UTF-8 字节；
3. 用 Ed25519 私钥签名，得到 64-byte 签名；
4. Base64 编码，写入 manifest 的 `signature.signature` 字段。

### 1.3 验证

```bash
python scripts/jwks/jwks_tool.py verify .well-known/mcp.json
# → {"valid": true, "kid": "swarmlabs-tools-2026-primary", "error": null}
```

验证失败场景：
- 篡改 manifest 任何字段 → signature 与 canonical bytes 不匹配 → invalid
- `signature` 是占位符（`[待发布签名密钥后填充]`）→ 直接拒绝
- `keyId` 不在 jwks.json 中 → 找不到公钥
- `keyId` 在 jwks.json 中被标记 `revoked_at` → 拒绝

### 1.4 密钥轮换

```bash
# 生成新密钥（追加，不覆盖旧的）
python scripts/jwks/jwks_tool.py generate-keys --kid "swarmlabs-tools-2027-primary" --rotate

# 旧密钥标记为 revoked（在 jwks.json 中手工或自动化添加 revoked_at）
# 旧密钥仍然保留在 jwks.json，因为历史 manifest 仍需要验证
```

策略：**追加 + 标记 revoked**。旧的 manifest 用旧 keyId 验证仍通过；新的 manifest
用新 keyId 签发；验证器对 `revoked_at` 的 key 一律拒绝。

---

## 二、JWKS 格式

```json
{
  "keys": [
    {
      "kty": "OKP",
      "crv": "Ed25519",
      "kid": "swarmlabs-tools-2026-primary",
      "alg": "EdDSA",
      "use": "sig",
      "x": "<base64url 编码的 32-byte 公钥>",
      "created_at": "2026-09-22T...",
      "revoked_at": null
    }
  ]
}
```

字段说明：
- `kty: OKP` = Octet Key Pair，JWA RFC 7518 §6.2
- `crv: Ed25519` = Ed25519 曲线
- `kid` = Key ID，manifest 的 `signature.keyId` 必须匹配
- `x` = Ed25519 公钥（32 bytes），base64url 编码
- `revoked_at` = 一旦设置，该 key 不再用于验证新签名（但历史 manifest 仍可通过其他方式验证）

---

## 三、验证流程（第三方 agent 视角）

1. 从 `https://swarmlabs.tools/.well-known/mcp.json` 拉取 manifest；
2. 从 `https://swarmlabs.tools/.well-known/jwks.json` 拉取 JWKS；
3. 读取 manifest 的 `signature.keyId` → 在 JWKS 中找到对应公钥 `x`；
4. 计算 manifest 的 canonical bytes（剔除 `signature` 字段，按 key 排序）；
5. 用 `x` 解码公钥，用 Ed25519 verify(canonical, base64(signature))；
6. 通过 = SwarmLabs 官方签发；失败 = 篡改或未授权修改。

**最小实现**（Python + PyNaCl，约 15 行）：

```python
import json, base64
from nacl.signing import VerifyKey
from nacl.exceptions import BadSignatureError

manifest = json.loads(fetch("https://swarmlabs.tools/.well-known/mcp.json"))
jwks = json.loads(fetch("https://swarmlabs.tools/.well-known/jwks.json"))

kid = manifest["signature"]["keyId"]
sig_b64 = manifest["signature"]["signature"]
key = next(k for k in jwks["keys"] if k["kid"] == kid and not k["revoked_at"])
vk = VerifyKey(base64.urlsafe_b64decode(key["x"] + "=" * ((4 - len(key["x"]) % 4) % 4)))

body = {k: v for k, v in manifest.items() if k != "signature"}
canonical = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
try:
    vk.verify(canonical, base64.b64decode(sig_b64))
    print("manifest is authentic")
except BadSignatureError:
    print("manifest was tampered with")
```

---

## 四、安全边界

### 4.1 私钥保管

- `.well-known/keys/` 已在 `.gitignore` 中排除
- 私钥只保存在本地开发机 + 备份密钥库
- 生产部署时通过 CI 环境变量或密钥管理服务（如 Vault）注入，不写入仓库

### 4.2 签名覆盖范围

签名覆盖 manifest **除 `signature` 字段本身以外的所有内容**，包括：
- capabilities、requiredScopes、auth、mcpServers
- version、author、description
- 但不包括 signature 本身（避免递归签名）

任何对 capabilities 或 scopes 的篡改都会导致签名验证失败。

### 4.3 已知限制

1. **JWKS 端点自身的可用性**：如果 jwks.json 被替换，攻击者可以用自己的密钥签名。
   缓解措施：manifest 中嵌入 JWKS URL，让验证方明确知道要拉哪个端点。
2. **无时间戳**：当前 signature 不含 `issued_at` 字段。缓解：manifest 里的 `version`
   字段间接提供版本信息；后续可加 `signature.issued_at` 字段。
3. **无过期**：JWKS 中 `revoked_at` 目前需要手工设置；后续可自动化。

---

## 五、测试

- `tests/test_jwks.py`：10 个单元测试，覆盖生成/签名/验证/篡改/占位符/未知 keyId/
  轮换/规范化稳定性
- `python scripts/jwks/jwks_tool.py selftest`：端到端自检（3 条路径）

**当前状态**：所有 10 项测试通过，自签 + 自验通过。

---

## 六、发布步骤（生产部署）

1. 生成密钥：`python scripts/jwks/jwks_tool.py generate-keys --kid "swarmlabs-tools-2026-primary"`
2. 签名 manifest：`python scripts/jwks/jwks_tool.py sign .well-known/mcp.json --key ... --apply`
3. 提交代码：`.well-known/jwks.json`（公开）+ 更新后的 `.well-known/mcp.json`（含签名）
4. CF Pages 部署：`.well-known/jwks.json` 自动通过 CF Pages 托管
5. 验证：`curl https://swarmlabs.tools/.well-known/jwks.json` 应返回 JWKS JSON

**⚠️ 不要在 CI 中跑 `generate-keys`** —— 每次生成都会创建新密钥，CI 应只做签名（用已存在的密钥）和验证。
