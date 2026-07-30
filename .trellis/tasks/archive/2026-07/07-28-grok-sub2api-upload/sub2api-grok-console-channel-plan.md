# sub2api 走 Grok Console 通道方案(让 multi-agent 搜索不再 402)

> 沉淀日期:2026-07-28
> 范围:解释 sub2api 调 `grok-4.20-multi-agent` 报 402 的根因,给出在「单服务(只跑 sub2api)」约束下的解决路径。
> 与本任务的关系:`grok-sub2api-upload` 只负责把 OIDC 凭证上传到 sub2api;**上传凭证无法解决 multi-agent 的 402**(见下文根因)。本方案是针对 402 的独立改造路径。

---

## 1. 问题与根因

**现象**:sub2api 上调用 `grok-4.20-multi-agent` 报:
```
Grok Responses API returned 402
{"code":"personal-team-blocked:spending-limit",
 "error":"You have run out of credits or need a Grok subscription...upgrade at https://grok.com/supergrok"}
```

**根因**:同一个 Grok 账号有**两套凭证、两条上游通道、两本账**。

| 维度 | sub2api(报 402) | grok2api(能用) |
|---|---|---|
| 凭证 | xAI OIDC `access_token` | Grok **SSO cookie** |
| 上游通道 | **Responses API**(`cli-chat-proxy.grok.com/v1`,回退 `api.x.ai`) | **Grok Console**(`console.x.ai/v1/responses`) |
| 认证头 | `Authorization: Bearer <access_token>` | `Cookie: sso=…` + `Authorization: Bearer anonymous`(占位) |
| 计费账本 | **API credits**,personal-team 有消费上限 | **网页订阅配额**,无 spending-limit 校验 |
| 结果 | `personal-team-blocked:spending-limit`(402) | 正常 |

**关键**:`personal-team-blocked:spending-limit` 是 grok 官方对 personal-team 账号在 **Responses API** 上的消费限额。买了 SuperGrok 网页订阅 ≠ 有 Responses API 额度。grok2api 之所以不报错,是因为它用 SSO 走 `console.x.ai` 网页态通道,根本不碰 Responses API 的 API-credits 计费。换模型/换推理档无效——卡点在通道计费,不在模型。

sub2api 把 402 当**账号级**处理(`openai_gateway_grok.go:1362` → 账号下线 30 分钟),印证这是账号在该通道的限额,不是某个模型的问题。

---

## 2. grok2api 的三通道(核实结论)

`grok2api` 源码核实(`C:\idealProject\github\grok2api`):

| 通道 | 上游端点 | 凭证 | 计费 | 有 multi-agent? |
|---|---|---|---|---|
| Grok Web | `grok.com/rest/app-chat/…` | SSO cookie | 网页订阅 | ❌ |
| **Grok Console** | **`console.x.ai/v1/responses`** | **SSO cookie** | 网页订阅 | ✅ **multi-agent 在此** |
| Grok Build(CLI) | `cli-chat-proxy.grok.com/v1/responses`(fallback `api.x.ai`) | OIDC access_token | **API credits** | 远程目录 |

- multi-agent 是 **Console 通道独占**:`console/catalog.go:24-31` 注册 `grok-4.20-multi-agent-0309`;`web/catalog.go` 完全没有。所有 multi-agent 别名(low/medium/high/xhigh)硬编码到 `Provider=grok_console`(`console/catalog.go:45-57`)。
- Console 认证:`console/headers.go:19,21` → `Authorization: Bearer anonymous` + `Cookie: sso=<token>; sso-rw=<token>`。`Bearer anonymous` 是占位,真身份在 SSO cookie。
- Console 的 403 被当 Cloudflare 挑战处理(`console/definition.go:27` `RetryForbiddenAsEgress`),不当 API 计费错误。
- `api.x.ai` 在整个 grok2api 里**只作为 Build 通道的 fallback**(`cli/fallback.go:138`),SSO 账号进不了 Build 通道,所以 multi-agent+SSO 永远不会触达会 402 的 `api.x.ai`。

---

## 3. 约束

- **单服务**:只跑 sub2api,不再起 grok2api(拒绝「sub2api 挂 grok2api 当上游」的方案,它违背约束)。
- **目标**:用 `grok-4.20-multi-agent` 做**搜索**(DeepSearch / 多智能体)。
- **不想花钱优先**:倾向不改代码、不买 API credits 的路径。

---

## 4. 方案 A:零代码(自定义上游 + Cookie 覆写)—— 推荐先试

sub2api 新增账号页面自带两个开关,组合起来等于「让现有 grok 账号伪装成走 Console 通道」,**不用改一行代码**。

### 配置

```
平台: Grok
账号类型: OAuth(用现有号,编辑即可)
自定义上游地址: https://console.x.ai/v1
请求头覆写(开启):
  Cookie : sso=<你的SSO token>; sso-rw=<你的SSO token>
```

SSO token 来源:grok-register 注册产出、导入 grok2api 时用的那个纯 SSO 值(见 `grok-register/account_outputs.py:414` 的 `grok-web-sso.txt`)。

### 六个卡点的真实状态

| 卡点 | 状态 | 证据 / 说明 |
|---|---|---|
| ① 上游填 `console.x.ai` | ✅ 能填 | `grok_upstream_url.go:42-58`:自定义地址走 `grokOperatorPolicyValidator`,`URLAllowlist.Enabled=false`(默认)时只校验格式,**不卡域名** |
| ② Cookie 覆写 | ✅ 大概率能 | `frontend/.../accounts.ts:717`:禁止覆写列表只有 `authorization`/`x-api-key`/连接控制头,**Cookie 未禁** |
| ③ Authorization 改不了 | ⚠️ 赌 | `openai_gateway_grok.go:1073` 硬发 `Bearer <access_token>`;但 Console 主验 Cookie,可能忽略它 |
| ④ 请求体协议 | ❌ 最大疑点 | sub2api 发 Responses API(cli-chat-proxy)格式;console.x.ai schema 可能不认 multi-agent 参数。grok2api 专门写了 `console/adapter.go` 做转换,sub2api 没有 |
| ⑤ Cloudflare | ❌ 高风险 | `console.x.ai` 在 CF 后;sub2api 的 TLS 指纹是 Node.js profile(`pkg/tlsfingerprint/dialer.go:55`)、ALPN 无 h2,可能被挑战拦 |
| ⑥ SSO 持久性 | ⚠️ | 静态填的 SSO 会过期/轮换,需定期手动更新 |

### 测试方法(先探链路,再测搜索)

先别直接打 multi-agent,用轻量模型探链路:
```bash
curl {sub2api}/v1/chat/completions \
  -H "Authorization: Bearer {sub2api key}" \
  -d '{"model":"grok-4.5","messages":[{"role":"user","content":"ping"}]}'
```
通了再换 `grok-4.20-multi-agent-0309`。

### 按返回判断卡点

| 返回 | 含义 | 下一步 |
|---|---|---|
| 200 正常 | 链路通 | 换 multi-agent 测搜索 |
| 400 / 422 请求体错 | ❹ 协议不兼容 | 零代码没救,转方案 B |
| 403 + HTML | ❺ 被 CF 挡 | 两方案都要解决 CF |
| 401 | Cookie 没生效 / SSO 失效 | 检查 SSO 值与覆写 |
| 402 spending-limit | 没走到 console | 检查自定义上游配置是否生效 |

---

## 5. 方案 B:新增 `grokSearch` 平台 —— 后备(零代码失败后)

在 sub2api 新增一个**与现有 grok 完全隔离**的独立平台,走自己的 SSO+Console 链路,不动现有 grok 一行代码。

### 隔离性(优于改老 grok)
- 现有 grok 路径不会被新平台触达(分发按 `account.Platform` 字面量精确匹配)。
- 核心侵入仅 `openai_gateway_forward.go:101` 一处 `else if` + `constants.go` 一个常量。
- 白名单 `baseURLAllowedHosts`(`oauth.go:46`)是 grok 局部的,新平台自拼 URL 可完全绕开(`console.x.ai` 全仓零引用)。

### 工作量(platform agent 核实)
- **最小 PoC(只求 console.x.ai+SSO 跑通):~250–350 行**
  - `constants.go` 加 `PlatformGrokSearch`(2 行)
  - 新建 `openai_gateway_grok_search.go`:自拼 `https://console.x.ai/v1/responses` + `Cookie: sso=` + `Bearer anonymous`,走 `s.httpUpstream.Do`,复用现有 stream/non-stream 处理(~200 行)
  - `openai_gateway_forward.go:101` 后加一行分发(3 行)
  - 账号:直接 SQL 插 `platform="grok_search"`、`credentials={"sso_token":"..."}`(`accounts` 表无 CHECK)
- **完整(含前端/migration/导入接口/模型目录):后端 6–10 文件 800–1500 行 + 前端 4–6 文件 300–600 行**

### 核心风险(与方案 A 共享)
1. **Cloudflare(最高)**:console.x.ai 在 CF 后,utls Node.js profile 大概率不够,JS challenge/Turnstile 无法靠 TLS 指纹过 → 需外挂 FlareSolverr 或自抓 Chromium profile。
2. **SSO 保活(次高)**:SSO cookie 有时效/轮换,无标准刷新协议,需持续运营(对比:现有 `ConvertSSOToBuild` 走 device-flow 自动转 token 更稳,但会绕回 OAuth 计费)。
3. 请求体协议差异、模型名路由冲突(`composite_platform.go:134` 把 `grok-*` 绑死老平台)。

---

## 6. 决策路径

```
              ┌─────────────────────────────────────┐
              │ 配置方案 A(自定义上游 + Cookie 覆写) │
              │ 用 grok-4.5 探链路                   │
              └──────────────────┬──────────────────┘
                                 ▼
                         返回 200 正常?
                    ┌────────────┴────────────┐
                  是                         否
                    │                         │
            换 multi-agent            看错误类型:
            测搜索                    ├ 400/422 → 协议(❹)
            ① 通 → 完事                ├ 403+HTML → CF(❺)
            ② 不通 → 见右              ├ 401 → Cookie/SSO
                                        └ 402 → 上游没生效
                    │
                    ▼
        ❹ 协议不兼容 / ❺ CF 挡 / ⑥ SSO 要持久化
                    │
                    ▼
          评估方案 B(新增 grokSearch 平台)
          注意:❺ CF 是 A/B 共同硬障碍,
                先解决 CF(FlareSolverr/Chromium 指纹)再投入 B
```

---

## 7. 关键证据清单(文件:行号)

**sub2api(`C:\idealProject\github\sub2api`)**
- `backend/internal/domain/constants.go:20-27` — platform 枚举(无 SSO 常驻类型)
- `backend/internal/pkg/xai/oauth.go:26,46,288` — `DefaultCLIBaseURL`、`baseURLAllowedHosts`、`ValidateTrustedBaseURL`
- `backend/internal/service/grok_upstream_url.go:12-58,70` — **自定义上游默认只校验格式(不过白名单)**、URL 构造
- `backend/internal/service/openai_gateway_grok.go:1064-1100,1362` — `buildGrokResponsesRequest`(Bearer)、402 账号级冷却
- `backend/internal/service/openai_gateway_forward.go:101` — grok 单 forwarder 路由分发点
- `backend/internal/service/grok_token_provider.go:66` — 强制 oauth
- `backend/internal/pkg/xai/sso_device.go:58` — SSO 仅一次性兑换为 OAuth token(`ConvertSSOToBuild`),不解决 402
- `backend/internal/pkg/xai/models.go:12-26` — grok 模型目录(含 `grok-4.20-multi-agent-0309`)
- `backend/internal/pkg/tlsfingerprint/dialer.go:55` — utls 底座,默认 Node.js profile
- `frontend/src/i18n/locales/zh/admin/accounts.ts:715-717,738-739` — 「请求头覆写」(禁覆写 authorization/x-api-key)、「自定义上游地址」文案
- `frontend/src/components/account/CreateAccountModal.vue:3787-3817` — 两个开关写入 credentials

**grok2api(`C:\idealProject\github\grok2api`)**
- `backend/internal/infra/provider/console/catalog.go:24-31,45-57` — multi-agent 是 Console 独占
- `backend/internal/infra/provider/console/headers.go:19,21` — `Bearer anonymous` + `Cookie: sso=`
- `backend/internal/infra/provider/console/adapter.go:300-305` — `console.x.ai/v1/responses`
- `backend/internal/infra/egress/manager.go:1985` — `BuildSSOCookie`
- `backend/internal/infra/egress/flaresolverr.go` — FlareSolverr clearance
- `backend/internal/infra/provider/definition.go:27,79,146` — `RetryForbiddenAsEgress`、多 Provider、`AuthTypeSSO`

**grok-register(当前项目)**
- `account_outputs.py:399-451` — 导入 grok2api 走 `/accounts/web/import`,SSO token(`grok-web-sso.txt`)
- `cpa_xai/schema.py:9` — OIDC 凭证 `base_url=https://cli-chat-proxy.grok.com/v1`(上传到 sub2api 走 Responses,必然 402)
