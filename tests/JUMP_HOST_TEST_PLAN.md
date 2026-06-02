# 跳板机功能测试计划

## 测试目标

验证 rtmux 跳板机（jump host）功能的完整性和正确性，包括：
- 单层/多层跳板机连接
- 命令执行和文件操作
- 配置持久化
- 错误处理

## 前置条件

- 本地机器可访问跳板机（SSH 端口开放）
- 跳板机可访问目标主机
- 有跳板机和目标主机的登录凭据（用户名 + 密码）

## 测试用例

### TC1: 单层跳板机连接

**目标**: 验证通过单个跳板机连接目标主机

**步骤**:
```bash
# 1. 配置跳板机
rtmux connect user@bastion --port 22 --password 'xxx'

# 2. 通过跳板机配置目标主机
rtmux connect user@target --port 22 --via <bastion_alias> --password 'xxx'

# 3. 验证连接配置
rtmux --json connections
# 预期: target 的 via 字段包含 bastion_alias

# 4. 验证密钥认证
rtmux --json exec test-session "hostname" --host <target_alias> --auto-create
# 预期: 返回目标主机的 hostname
```

**预期结果**:
- 跳板机只生成 key，不安装 tmux
- 目标主机生成 key + 安装 tmux
- 连接配置中 via 字段正确

---

### TC2: 多层跳板机连接

**目标**: 验证多层跳板机链（本地 → A → B → 目标）

**步骤**:
```bash
# 1. 配置第一层跳板机 A
rtmux connect user@A --port 22 --password 'xxx'

# 2. 配置第二层跳板机 B（通过 A）
rtmux connect user@B --port 22 --via <A_alias> --password 'xxx'

# 3. 配置目标主机（通过 A,B）
rtmux connect user@target --port 22 --via <A_alias>,<B_alias> --password 'xxx'

# 4. 验证连接
rtmux --json exec test-session "hostname" --host <target_alias> --auto-create
```

**预期结果**:
- 连接链正确建立
- 命令通过 A → B → 目标 执行

---

### TC3: 命令执行测试

**目标**: 验证通过跳板机执行各种命令

**步骤**:
```bash
# 基础命令
rtmux --json exec test "hostname && whoami" --host <alias> --auto-create

# Base64 模式（特殊字符）
rtmux --json exec test 'echo "hello $USER" | grep hello' --host <alias> --base64

# 长时间命令
rtmux --json exec test "sleep 5 && echo done" --host <alias> --timeout 30

# 多行命令
rtmux --json exec test 'for i in 1 2 3; do echo "item $i"; done' --host <alias> --base64
```

**预期结果**:
- 所有命令正常执行
- 输出正确返回
- 退出码正确传播

---

### TC4: 文件操作测试

**目标**: 验证通过跳板机进行文件操作

**步骤**:
```bash
# 上传文件
echo "test content" > /tmp/test.txt
rtmux --json cp /tmp/test.txt ::/tmp/test.txt --host <alias>

# 验证上传
rtmux --json exec test "cat /tmp/test.txt" --host <alias>

# 下载文件
rtmux --json cp ::/tmp/test.txt /tmp/downloaded.txt --host <alias>

# 验证下载
cat /tmp/downloaded.txt

# 列出文件
rtmux --json ls /tmp --host <alias>

# 删除文件
rtmux --json rm /tmp/test.txt --host <alias>
```

**预期结果**:
- 文件上传下载正确
- 文件内容一致
- 列表和删除正常

---

### TC5: 会话管理测试

**目标**: 验证通过跳板机管理 tmux 会话

**步骤**:
```bash
# 创建会话
rtmux --json new test-session --host <alias>

# 列出会话
rtmux --json list --host <alias>

# 捕获历史
rtmux --json capture test-session --host <alias>

# 关闭会话
rtmux --json kill test-session --host <alias>
```

**预期结果**:
- 会话创建、列出、捕获、关闭正常

---

### TC6: 配置持久化测试

**目标**: 验证 via 字段正确保存和读取

**步骤**:
```bash
# 1. 配置跳板机连接
rtmux connect user@target --via <bastion_alias> --password 'xxx'

# 2. 检查配置文件
cat ~/.remote-ops/connections.json
# 预期: target 的配置包含 "via": "<bastion_alias>"

# 3. 检查 connections 命令输出
rtmux connections
# 预期: 显示跳板机列

# 4. 重启后验证（重新打开终端）
rtmux --json exec test "hostname" --host <target_alias> --auto-create
```

**预期结果**:
- via 字段正确保存到 JSON
- connections 命令正确显示
- 重启后连接仍可用

---

### TC7: 错误处理测试

**目标**: 验证各种异常情况的处理

**用例 7.1: 跳板机未配置**
```bash
rtmux connect user@target --via nonexistent --password 'xxx'
# 预期: 错误提示 "跳板机 nonexistent 未配置，请先初始化"
```

**用例 7.2: 跳板机不可达**
```bash
rtmux connect user@target --via <unreachable_alias> --password 'xxx'
# 预期: 连接超时错误
```

**用例 7.3: 目标主机不可达（通过跳板机）**
```bash
rtmux --json exec test "hostname" --host <alias>
# 预期: 连接失败错误
```

**用例 7.4: 认证失败**
```bash
rtmux connect user@target --via <alias> --password 'wrong'
# 预期: 认证失败错误
```

**预期结果**:
- 错误信息清晰
- 不会泄露敏感信息
- 退出码正确

---

### TC8: attach 命令测试

**目标**: 验证 attach 命令支持跳板机

**步骤**:
```bash
# 创建会话
rtmux --json new test-session --host <alias> --auto-create

# 附加到会话（需要手动测试，因为是交互式的）
rtmux attach test-session --host <alias>
# 预期: 通过跳板机连接到远程 tmux 会话
```

**预期结果**:
- 通过跳板机成功附加到远程会话

---

## 测试环境

| 角色 | 主机 | 端口 | 用户 |
|------|------|------|------|
| 本地 | - | - | - |
| 跳板机 A | 待定 | 22 | 待定 |
| 跳板机 B | 待定 | 22 | 待定 |
| 目标主机 | 待定 | 22 | 待定 |

## 测试执行顺序

1. TC1: 单层跳板机连接（基础功能）
2. TC6: 配置持久化测试（验证配置正确）
3. TC3: 命令执行测试（核心功能）
4. TC4: 文件操作测试（文件功能）
5. TC5: 会话管理测试（会话功能）
6. TC2: 多层跳板机连接（高级功能）
7. TC7: 错误处理测试（异常情况）
8. TC8: attach 命令测试（交互功能）

## 风险和注意事项

1. **网络环境**: 测试需要访问真实的跳板机和目标主机
2. **凭据安全**: 测试密码不要提交到代码库
3. **超时设置**: 跳板机连接可能较慢，适当增加超时
4. **清理**: 测试完成后清理临时文件和会话
