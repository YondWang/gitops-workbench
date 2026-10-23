# 仿真测试与云平台联调说明

适用：当前 `notify_sim_test` 与 Workbench 实现，2026-11-04。

## 1. 交互流程

```text
Workbench 创建测试任务（task_id）并触发 Pipeline
  → 编译 x86 仿真包 → 上传云盘
  → notify_sim_test：HTTP POST 任务给云平台
  ← 云平台快速返回 2xx，表示已受理；CI job 随后结束
  → 云平台异步下载包、执行测试
  → 云平台回调 Workbench：running → passed / failed，附报告
  → Workbench 更新任务状态、保存报告
```

**等待结果由 Workbench 负责，GitLab job 不阻塞等待。** 本次日志 `SIMOS_SIM_NOTIFY_TARGETS is empty` 表示通知地址尚未配置，实际还没有发送 HTTP 请求。

## 2. 我方 → 云平台：创建测试任务

云平台提供一个 HTTPS POST 地址（路径由云平台确定，例如 `/api/sim-tasks`），接收：

```http
Content-Type: application/json
Authorization: Bearer <云平台分配的通知 token>
```

请求示例（占位地址、校验值需替换）：

```json
{
  "event": "sim_test_task_created",
  "task_id": "SIMTEST-20261104140800-a1b2c3",
  "tag": "feature-v3.2_x86ci_V3.2.10.43_202611041408",
  "version": "3.2.10.43",
  "commit": "868fc267...",
  "test_target": "sim",
  "note": "仿真回归",
  "packages": {
    "x86": {
      "name": "simos_x86_sim_feature-v3.2_x86ci_V3.2.10.43_202611041408.tar.gz",
      "size": 123456,
      "md5": "<md5>",
      "sha256": "<sha256>",
      "registry_url": "https://<GitLab>/api/v4/projects/20/packages/generic/simos-sim-x86/<tag>/<文件名>",
      "cloud_dir": "/public/Versions/2026-11-04_V3.2.10.43/车机/CI自动构建/sim-x86"
    },
    "arm": null
  },
  "pipeline": {"id": "<pipeline_id>", "url": "<pipeline_url>"},
  "callback": {
    "result_url": "https://www.chancee-shanghai.cn:9910/api/sim-test-results",
    "token": "<双方约定的回调 token>",
    "instruction": "测试结束后 POST {task_id, status, report_url|report 文件} 至 result_url"
  }
}
```

关键约定：

- `task_id` 是全流程关联键；云平台必须按它去重，通知重试或手动重发不能重复创建测试。
- `test_target`：`sim` 仿真、`vehicle` 实车、`both` 两者；当前一个 task 只有一个最终状态。`both` 如何汇总、由谁汇总需双方确认，接口尚未定义仿真/实车各自的结果字段。
- `packages.arm` 有包时为 `{resident, resident_variants, debs}`；resident 条目包含 `name/registry_url/cloud_dir/md5/sha256`，variants 另有 `label`，debs 条目为 `label/name/registry_url/cloud_dir`。字段可能为空或缺失，单配置不能假设固定为 360/360s，以实际 payload 为准。
- `cloud_dir` 是云盘逻辑目录，**不能直接作为 HTTP 下载 URL**。下载可选 GitLab `registry_url` 或云盘，访问方式和只读凭据需双方确定。payload 不提供 GitLab 下载凭据，不应依赖已结束 job 的 `CI_JOB_TOKEN`。
- 云平台受理后立即返回 `200` 或 `202`；响应 body 当前不解析，建议 `{"accepted":true,"task_id":"..."}`。返回 2xx 只代表受理，不代表测试通过。
- CI 每个目标最多尝试 3 次，每次 HTTP 最长 60 秒，失败后间隔 5 秒、15 秒重试。请先可靠入队再返回 2xx。Workbench 手动重发使用相同 POST 协议，单次请求超时 30 秒。

## 3. 云平台 → 我方：状态与报告回调

回调 `callback.result_url`，使用独立的回调 token：

```bash
curl --fail-with-body --request POST \
  'https://www.chancee-shanghai.cn:9910/api/sim-test-results' \
  --header "X-Sim-Test-Token: ${CALLBACK_TOKEN}" \
  --header 'Content-Type: application/json' \
  --data '{"task_id":"SIMTEST-20261104140800-a1b2c3","status":"running"}'
```

完成时同一接口提交：

```json
{
  "task_id": "SIMTEST-20261104140800-a1b2c3",
  "status": "passed",
  "summary": "共 120 项，通过 120 项",
  "finished_at": "2026-11-04T17:30:00+08:00",
  "report_url": "https://<云平台>/reports/<task_id>/report.html"
}
```

`task_id/status` 必填；status 仅支持 `running/passed/failed`，其余字段选填。`finished_at` 建议带时区，省略则使用我方接收时间。报告 URL 应允许 Workbench 直接 GET 下载，当前不附加云平台鉴权头；需要鉴权时使用可访问的签名 URL，或直接上传文件：

```bash
curl --fail-with-body \
  'https://www.chancee-shanghai.cn:9910/api/sim-test-results' \
  --header "X-Sim-Test-Token: ${CALLBACK_TOKEN}" \
  --form 'task_id=SIMTEST-20261104140800-a1b2c3' \
  --form 'status=passed' --form 'summary=全部通过' \
  --form 'report=@report.html'
```

每次支持一个 `report` 文件，上限 100 MiB；可用单文件 HTML 或打包报告。成功返回 HTTP `200`，body 为 `{"ok":true,"task":{...}}`。错误：`400` 参数错误、`401` token 无效、`404` task 不存在、`503` 我方未配置 token、`500` 服务异常。网络失败/5xx 由云平台重试；4xx 先排查参数与配置。请顺序发送回调，最终结果后停止发送 running；当前接口不保证乱序状态保护。报告下载/云盘归档失败也可能返回 200，应同时查看返回任务的 `error/report`。

## 4. 等待、超时与可选查询

- Workbench 通知成功后进入 `notified`，收到回调进入 `running/passed/failed`。默认等待窗口 **24 小时**，任务页可调整；每次 running 回调重置等待截止时间，长任务应定期回传 running。
- 到期未收到有效进度/结果时，Workbench 在任务刷新时标记 `timeout`；这不会自动取消云平台任务。
- 首轮联调使用回调即可。可选兜底查询：云平台提供 `GET <状态地址>/{task_id}`，我方配置 `SIMOS_TEST_PLATFORM_STATUS_URL`（含 `{task_id}` 占位符）。请求头为同一 `X-Sim-Test-Token`，直接返回 `{status, summary, report_url, finished_at}`，不要再包一层 `data`。默认最短查询间隔 60 秒、单次超时 10 秒；由后台/页面任务刷新触发，不是 CI 持续轮询。查询仅合并状态与报告链接，文件保存使用回调。

## 5. 双方配置与最短联调步骤

| 配置位置 | 配置内容 |
| --- | --- |
| SimOS GitLab CI/CD Variables | `SIMOS_SIM_NOTIFY_TARGETS=[{"name":"cloud-platform","url":"<云平台接收地址>","token":"<通知token>","enabled":true}]` |
| SimOS GitLab CI/CD Variables | `SIMOS_SIM_NOTIFY_CALLBACK_URL=https://www.chancee-shanghai.cn:9910/api/sim-test-results`；`SIMOS_SIM_NOTIFY_CALLBACK_TOKEN=<回调token>` |
| Workbench 生产 `.env` | 同一个 `SIMOS_SIM_NOTIFY_CALLBACK_TOKEN`；`GITOPS_SIM_RESULT_URL` 为上述回调地址；`GITOPS_SIM_NOTIFY_TARGETS` 与 CI 的目标数组一致，供手动重发使用 |
| 云平台 | 通知鉴权 token、按 task_id 去重、包下载权限、回调地址与 token；确认能访问我方 HTTPS 9910 端口 |

1. 云平台先提供接收 URL、通知 token，双方确定包下载方式；回调 token 与通知 token 分开配置，通过安全渠道交换。
2. 从 Workbench 创建一个测试任务，确保 task 已存在；配置后重试对应 `notify_sim_test`。查看 `notify-payload.json` 和 `notify-result.json`，确认云平台收到并只创建一个任务。产物中的回调 token 为 `***`，实际 POST 为真实 token，不能拿 `***` 回调。
3. 云平台下载并校验 x86 包，回调一次 running，再回调 passed/failed 和报告；双方核对任务页状态、摘要和报告。
4. 重发同一通知验证去重，再验证一次 failed；`both` 的汇总规则、实车调度和云盘下载鉴权由云平台确认后再开展对应联调。
