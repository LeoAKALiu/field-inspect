# 访问身份与部署

默认未配置密钥时仅接受无代理头的本机访问；该身份是 local_operator，不代表个人认证。ASTRA_API_TOKEN 保留共享操作密钥模式。

## 独立身份模式

FIELD_AUTH_FILE 指向私有 JSON 数组，每项严格包含 id、role、token_sha256。id 为 1—64 个字母、数字、点、下划线或连字符；role 是 viewer/reviewer/operator。只存放高熵令牌的 SHA-256，不能用人类密码的快速哈希代替。文件存在配置时优先于共享密钥；文件错误、缺失或重复身份时拒绝访问，不降级为本机免认证。

以部署管理员身份在 services/api 执行：

```powershell
uv run --frozen --no-dev python scripts/create_credential.py --file C:/FieldInspect/private/users.json --token-file C:/FieldInspect/private/alice-token.txt --id alice --role operator
$env:FIELD_AUTH_FILE = 'C:/FieldInspect/private/users.json'
```

脚本生成随机令牌，只写到指定文件，不打印令牌。按人员分别建立凭据；Windows 用 ACL 限制两个文件仅管理员可读，Linux 文件权限设为 0600。凭据发行操作必须串行执行；不要把私有目录放入源码、桌面或 iCloud 同步工作区。通过目标机正常方式把个人令牌交给对应人员，在网页访问授权输入。没有在本次开发中创建真实账户或令牌。

viewer 可读 HTTP/回放数据；reviewer 另可计算候选、追加人工关联复核；operator 可导入业务数据、登记设备、派生观测和管理批次。原始巡检包导入仍使用服务器本机 CLI，不新增上传入口。访问审计 GET /api/access/audit 仅 operator 可读。权限以服务端检查为准。

会话最多 8 小时，HttpOnly/SameSite；Cookie 写请求要求同源 Origin。每次请求重读凭据文件，删除条目或轮换摘要使对应令牌/会话失效；已有 WebSocket 会话在断开或服务重启后重新认证。外网部署须在入口配置 HTTPS，凭据文件不可由普通应用用户修改。原始令牌与摘要都不能公开。

独立身份模式中，复核 operator_label 使用已认证 id，业务审计同样使用认证身份。access_audit 保存写请求的身份、角色、方法、路径、响应码和时间，不保存请求体或令牌。该操作日志随 SQLite 备份保存，但不进入业务 ledger_digest，避免备份验证动作自身使业务摘要立即失效。日志的外部留存/OS 防篡改策略由部署环境负责。

## 容器

共享密钥模式：在私有环境设置 ASTRA_API_TOKEN 后使用 docker compose。独立身份模式：设置宿主机 FIELD_AUTH_FILE，然后使用 `docker compose -f docker-compose.yml -f docker-compose.auth.yml up -d --build`。认证文件只读挂载。未配置凭据时，反向代理访问被拒绝，不能把代理地址当作用户本机。

Docker 镜像不复制运行归档或数据库；仅运行时挂载持久目录。依赖构建使用冻结锁文件，不隐式重解依赖。容器启动后的健康检查是日常运维配置；本阶段未构建/启动容器，也未执行健康探测或权限测试。
