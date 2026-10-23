# bugfix/V3.2 双矩阵上传失败：云端 Runner 排查单

## 已确认的问题

流水线：[3551](https://www.chancee-shanghai.cn:9900/OS/simos/-/pipelines/3551)，tag `bugfix-V3.2_V3.2.7.171_202611041636`，提交 `c81ed30c97246cc655a1ec63e8d1abaebacc062d`。

| Job | 配置 | 构建 Pod |
| --- | --- | --- |
| [29923](https://www.chancee-shanghai.cn:9900/OS/simos/-/jobs/29923) | SIMBOT_R6_A / 360 | `runner-ppithnfsy-project-20-concurrent-2-ie2kesnv`，IP `10.0.247.160` |
| [29924](https://www.chancee-shanghai.cn:9900/OS/simos/-/jobs/29924) | SIMBOT_R6_B / 360s | `runner-ppithnfsy-project-20-concurrent-3-zg5hzs0k`，IP `10.0.247.159` |

- Namespace：`gitlab`；GitLab Runner #13，18.7.2；管理 Pod：`ros-build-gitlab-runner-5c4ddc978c-z4mms`，system ID `r_Brd7FaiTV5cV`。
- 两个 DEB job 都已编译和打包成功，上传首个 DEB 时 `curl: (6) Could not resolve host: www.chancee-shanghai.cn`。
- helper 上传 artifacts 同样失败：`lookup www.chancee-shanghai.cn on 172.16.0.10:53: write udp <Pod IP>:<随机端口>->172.16.0.10:53: write: operation not permitted`。
- 日志中的故障窗口约为 **2026-11-04 17:04–17:05（北京时间）**。Job 开始时源码获取成功，故障应结合长时间构建后的网络状态排查。
- 从 GitLab 直接读取失败 tag 的 CI：仍为原来的固定 A/B 双矩阵，未引入单配置 conditional include，ARM 镜像未变；下游 publish 的 artifacts 依赖完整。
- data1 上 GitLab HTTPS 可达，但这是内网访问结果，不能替代云端构建 Pod 的验证。

## 请云端 K8s 管理方检查

目标：恢复上述构建 Pod（含 build/helper 容器）访问 DNS 和 GitLab 的能力。当前证据确认 DNS UDP 发包被拒，尚不能判定具体是哪条 NetworkPolicy、CNI/ECI 或宿主网络规则导致。

```bash
# 原 Pod 可能已清理；不存在时请使用同一 Runner 创建的诊断 Pod。
kubectl -n gitlab get pod -o wide
kubectl -n gitlab get networkpolicy -o yaml
kubectl -n kube-system get svc kube-dns -o wide
kubectl -n gitlab describe pod <构建Pod>

# 对 build 和 helper 分别执行；以镜像已有工具为准。
kubectl -n gitlab exec <构建Pod> -c build -- cat /etc/resolv.conf
kubectl -n gitlab exec <构建Pod> -c build -- getent hosts www.chancee-shanghai.cn
kubectl -n gitlab exec <构建Pod> -c build -- curl -I --connect-timeout 10 https://www.chancee-shanghai.cn:9900/
kubectl -n gitlab exec <构建Pod> -c helper -- cat /etc/resolv.conf
```

1. 核对 `172.16.0.10` 是否为该集群正确且可达的 DNS 地址，检查 CoreDNS Service/Endpoint、Pod 路由与 CNI/ECI 状态。用具备 DNS 工具的诊断容器分别测试 UDP/TCP 53。
2. 检查适用于构建 Pod 的出站策略、安全组、CNI/ECI 网络规则；按实际网络路径恢复到 DNS 的 UDP/TCP 53，以及到 GitLab 的 TCP 9900。不要仅凭日志全局开放所有出站流量。
3. 如果采用域名固定映射作为应急方案：先从云端验证 GitLab 实际可达 IP 和 TLS，再配置 Runner 的 `[runners.kubernetes.host_aliases]`（TOML 中通常为数组表 `[[runners.kubernetes.host_aliases]]`），让新建 Pod 的 build/helper 都生效。**不要直接使用 data1 内网地址 `192.168.110.17`，云端路由尚未验证。** 单独给 curl 加 `--resolve` 无法修复 helper 的 artifacts 上传。
4. 配置变更前保留原 Runner Helm values/config；以新建 Pod 验证。Runner 配置修改通常不能修复已创建的构建 Pod，管理 Pod 的更新需避开现有构建任务或由运维安排。

## 验收与恢复发版

先用相同 Runner 的短任务验证：DNS 解析成功、HTTPS 证书校验通过、一个临时 Generic Package 文件上传成功、artifacts 上传成功，再重跑 DEB 编译，避免再次浪费约 30 分钟。

临时包上传只使用诊断任务本身的 `CI_JOB_TOKEN` 和独立诊断 package/version，不复用正式版本；验证后按运维流程清理。成功标准为 Package Registry PUT 返回 2xx、Runner artifacts 上传返回 201。

查询时已存在 DEB 重试 job 29933/29934（pending），本次排查未触发或取消任务。网络修复后检查现有任务状态再决定重试。失败 job 的 artifacts 上传未成功，原 Pod 清理后无法依赖其本地 DEB，需要重新编译。

当前没有集群管理入口，未修改云端 Runner/网络策略，也未修改 bugfix/V3.2 的双矩阵 CI。待管理方修复并完成上述验证，才能确认线上故障解除。

## 17:50 补充：重跑后任务挂起

2026-11-04 17:50（北京时间）通过 GitLab API 检查：

- 17:45 创建的新 resident job 29935/29936、DEB job 29942/29943 均为 `pending`，`runner=null`、`started_at=null`，尚未分配 Runner。
- Runner #13 未暂停，包含 `simos-resident-builder`、`simos-deb-builder` 标签，访问级别 `not_protected`，未发现标签/保护限制不匹配。
- Runner manager 最后心跳停在 **17:22:50**，检查时已约 **27 分钟**未更新；GitLab API 仍显示 online，不能以该标签判断进程是否正常。
- 旧 resident job 29921/29922 停在 `canceling`。发布 Runner #20 最后心跳 17:42，且只有发布标签，不能替代云端编译 Runner。

请优先检查 Runner 管理 Pod 的存活、重启次数和日志，以及管理 Pod 到 GitLab HTTPS 9900 的 DNS/连接。日志重点关注 `/api/v4/jobs/request`、认证失败、网络超时及并发槽未释放。恢复心跳和接单后，再验证前述构建 Pod 的 DNS/上传问题；两项都通过才能恢复发版。不要仅修改 job 标签或反复重试来绕过。
