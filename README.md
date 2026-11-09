# Krill 模型目录

只维护 Krill 原始模型和未乘账户、线路或用户分组倍率的价格。模型名称保持原样；不使用 models.dev、其他供应商或 New API 默认目录补充说明、标签、图标或能力。

当前快照：70 个价格条目，包含 47 个 token 模型、6 个按次模型、17 个按秒模型；其中 7 个 Fast、2 个 Ultrafast、16 个长上下文规则、2 个带峰谷规则的模型。采集时间和原始文件 SHA-256 见 `manifest.json`。

## 文件与同步入口

| 文件 | 用途 |
| --- | --- |
| `source/krill/model-pricing.json` | Krill `/api/model-pricing` 原始响应，原始字节保留 |
| `source/krill/capture.json` | 来源、采集时间与记录数量 |
| `api/krill/models.json` | 完整原始字段的可读版本，保留上下文、最大输出、模态、原始时间规则等 |
| `api/newapi/models.json` | New API 原生模型资料同步格式 |
| `api/newapi/vendors.json` | New API 原生厂商资料同步格式 |
| `api/i18n/{zh,en,ja}/newapi/{models,vendors}.json` | New API 按语言访问的相同资料，不添加翻译内容 |
| `api/newapi/ratio_config.json` | New API 原生上游价格同步格式，包含表达式和按秒基础单价 |
| `api/ratio_config.json` | 与上一文件相同的价格载荷 |
| `api/newapi/billing-units.json` | 计费单位清单，防止把视频每秒价误当成每次价 |
| `api/newapi/speed-models.json` | 原始 Standard / Fast / Ultrafast 型号对应关系；不设置渠道 |
| `adapter.json` | 格式适配所需但 Krill 原始响应没有明确提供的约定 |
| `validation/` | 格式、导入回读与离线价格核对结果 |

模型资料和价格沿用 New API 默认源的两个独立入口。不能把模型说明、价格、渠道设置都塞进一个 `models.json`，也不能把价格文件当成渠道配置导入。

## 数据原则

- 模型 ID、大小写、后缀与 Krill 精确一致，不新增型号或用别名替换型号。
- 说明、标签、图标、接口资料只有 Krill 提供时才使用。当前原始接口不提供这些字段，原生模型资料中的这些值为空。
- 在 New API 同步预览中选择这些空字段后，会清空原来的对应说明、标签、图标或模型自定义接口资料。不要误以为遗漏字段等于保留原值：当前原生元数据格式使用完整记录。
- 只使用原始标价。`route_multipliers` 等原始字段为完整存档而保留，但不应用它们；不导出用户组、不导出用户组倍率。
- 同步价格只管理价格。渠道模型列表、渠道类型、WS、参数覆盖、模型映射、上架选择等不由本目录自动修改。
- 峰谷、上下文与速度价格写进 `billing_expr`。简单 token 模型也使用表达式，避免 New API 内置模型默认规则覆盖源价格。
- 按次模型用 `fixed()` 表达式，不擅自添加每张图乘数、尺寸加价或质量加价。
- 按秒模型用原生 `model_price` 保存每秒单价，必须由按时长结算的视频适配器使用；普通固定每次计费或任意任务插件不是等价替代。
- 基础 OpenAI 型号收到 `service_tier=priority` 或 `fast` 时，使用 Krill 同名 `-fast` 条目的原价；原始 `-fast` 型号条目也独立保留。实际转发 `priority`、将客户端字面 `fast` 转为 `priority` 是渠道设置，本目录不修改它。
- Ultrafast 原始条目与价格完整保留，不臆造未确认的请求参数。

## 尚不能宣称完全等价的边界

原始数据完整保留、原生 JSON 导入不丢字段、最终上游实际账单一致，是不同层面的保证。

1. Krill 原始响应和价目页没有明确提供峰谷时区。当前 `adapter.json` 显式采用 `Asia/Shanghai`，并标记为未获 Krill 确认，不能把它冒充原始字段。
2. Krill 采用请求开始还是完成时刻判断峰谷尚未确认。当前 New API 的时间函数在求值时读取时钟，因此跨峰谷边界的长请求仍需另行确认。
3. 原生 New API 模型资料同步没有专门的最大上下文、最大输出和全部模态字段；这些数据保留在原始目录，不能声称都已通过原生元数据接口写入。
4. 原生上游价格同步不会配置渠道参数和路由。Fast 转发与别名路由需要渠道本身已经配置正确。
5. 按秒价格文件的单位已保留，格式测试没有执行真实视频生成，也没有证明任意新的视频适配器都按秒结算。

## 在 New API 接入公开目录

此仓库已于 2026-11-09 按所有者要求设为公开。只包含模型数据、转换工具和测试记录，没有密钥、Cookie 或账户凭据。

- 价格完整地址：`https://raw.githubusercontent.com/Xias2y/model/main/api/newapi/ratio_config.json`。
- 资料源根地址：`https://raw.githubusercontent.com/Xias2y/model/main`，对应模型管理的 `SYNC_UPSTREAM_BASE` 环境变量；原生同步会自动访问 `/api/i18n/zh/newapi/models.json` 等路径。
- 若使用接受 base_url 和 endpoint 的 API，base_url 可为 `https://raw.githubusercontent.com`，endpoint 为 `/Xias2y/model/main/api/newapi/ratio_config.json`；endpoint 也支持直接使用完整 HTTPS 地址。

当前 v1.0.0-rc.49 的上游价格同步前端只列出现有渠道和两个内置预设，没有新增独立价格源的按钮；基础地址也是只读显示，不能照上述两个参数直接新增一行。

该版自定义接口选择还有一处状态问题：选择 custom 时把 endpoint 置空，而渲染时空值回退为 /api/pricing，导致输入框无法稳定展开。这是按对应源码得出的结论，没有在本轮修改生产前端。

因此公开文件已经可以匿名下载，但要实现 GUI 中稳定选择 Krill 目录，还需添加 Krill 预设或修复并补齐自定义源入口。后端本身已支持完整 HTTPS endpoint。不要为接入价格源修改正常模型调用渠道的 Base URL。

模型资料未勾选“参与官方同步”时，原生资料同步会标为 blocked；价格同步是另一路径。资料与价格目前也是两个同步入口，不是仓库公开后自动合并成一个按钮。

本轮没有修改生产站点的同步源、数据库、分组、渠道、源码或 OpenResty。

## 更新目录

1. 从已授权的 Krill 价目请求获取完整 `/api/model-pricing` 响应，替换 `source/krill/model-pricing.json`。
2. 更新 `source/krill/capture.json` 中的采集时间和记录数量。不要导入账户、余额、密钥、Cookie、HAR 或请求头。
3. 运行 `python scripts/build.py`，生成 New API 原生 JSON。
4. 运行 `python scripts/check_prices.py`，核对所有型号与价格场景。
5. 需要验证新 New API 版本时，在有 Docker 的 Linux 主机运行 `python scripts/check_newapi.py --image <明确的镜像摘要>`。它创建并删除独立的临时实例，使用空的内存数据库，不连接生产数据库，不发出付费模型请求。
6. 审查 Git diff 后提交。需要发布到 New API 时再同步更新。

## 本轮验证

- 测试镜像：与当前生产相同的 `v1.0.0-rc.49` 固定摘要，见 `validation/newapi-import.json`。
- 原生资料同步：70 个模型成功创建，再次预览全部 unchanged。
- 原生价格同步抓取：70 个型号完整返回，与文件中的对应价格和表达式一致。
- 价格保存与回读：70 个型号完全一致，53 条表达式通过 New API 原生校验，17 个按秒基础单价保持不变。
- 离线价格核对：26,375 个场景，包括峰谷每天 1,440 分钟、上下文阈值前/等于/后、普通/Fast 参数分支。
- 没有进行真实模型或视频生成扣费测试。

## 格式参考

只参考格式与导入实现，不使用下面目录的模型数据：

- New API 源码提交：`48a6c87f9e7757fdc58e5be9bcb4f8281ad0dfd0`，`controller/model_sync.go`、`controller/ratio_sync.go`、`model/model_pricing_config.go`。
- New API 默认模型目录：`basellm/llm-metadata` 的 `src/services/newapi-builder.ts`、`src/billing/ratio-config.ts`。
- New API 的 models.dev 预设转换只生成基础倍率字段；本项目不走该格式，以免遗漏 Krill 的特殊价格规则。
