# VPS Radar · 补货雷达

在自己的服务器上运行的 VPS 库存监控工具。中文面板管理商家、机型、检测规则和邮件通知；后台持续检查，关闭浏览器不影响监控。

采用 **Python 3.12 + FastAPI + SQLite + 原生 HTML/CSS/JavaScript**。无需 Redis、独立数据库或前端编译。Docker Compose 单服务启动，可选 Caddy 自动 HTTPS。

## 已实现

- **7 家商家、13 个机型预设**：DMIT、VMISS、VMRack、BandwagonHost、ZgoCloud、VIRCS、DigitalFyre。
- **自由配置监控**：增删改商家/机型、地区、配置、价格备注、购买地址、检测间隔；搜索与状态筛选。
- **3 类检测器**：WHMCS 商品配置页及目录、HTML 单产品区域、公开 JSON 库存字段。
- **中文面板**：监控总览、最近库存变化、24 小时检测健康度、检测历史、邮件投递记录。
- **SMTP 邮件**：STARTTLS / SSL、多收件人、测试邮件、加密保存授权码、持久化队列和失败重试。
- **可靠调度**：连续确认、状态持久化、去重、失败退避、同站点串行、4 个并发检查、抖动错峰。
- **自部署**：Docker、Compose、一键脚本、健康检查、自动重启、日志轮转、自动 HTTPS。
- **管理功能**：管理员登录、修改密码、配置 JSON 导入导出、SQLite 一致性备份脚本。

## 一键部署

将当前仓库复制或 clone 到 Linux 服务器，在仓库根目录运行：

```bash
bash deploy.sh --domain radar.example.com
```

把 `radar.example.com` 替换为你的域名，提前将 DNS 指向服务器并开放 TCP 80/443。脚本会在没有 Docker 时使用 Docker 官方安装程序安装 Docker，自动生成 `.env` 中的随机管理员密码，构建镜像并启动服务。安装 Docker 时需要 root 或 sudo 权限。已有 Docker 时支持带 Compose v2 的 Linux 主机；自动安装流程推荐用于 Docker 官方支持的 Debian / Ubuntu。

访问 `https://你的域名`。首次管理员密码在服务器仓库的 `.env` 中查看。后台修改密码后，`.env` 的初始密码不再生效。

没有域名也可以先运行：

```bash
bash deploy.sh
# 在你自己的电脑建立隧道，然后访问 http://127.0.0.1:8080
ssh -L 8080:127.0.0.1:8080 user@server
```

默认只把应用端口绑定到服务器 `127.0.0.1`。需要交给已有 Nginx / Caddy 反向代理时，设置 `.env` 的 `APP_ORIGIN=https://你的域名` 和 `SECURE_COOKIES=true`，代理转发到 `127.0.0.1:8080`。`APP_ORIGIN` 必须与浏览器访问的协议、主机名、端口一致。

### 手动 Compose

```bash
cp .env.example .env
# 编辑 .env，设置至少 12 字符的唯一 ADMIN_PASSWORD
# 默认端口 8080；可以修改 PORT

docker compose up -d --build --wait
# 可选：配置 DOMAIN、APP_ORIGIN、SECURE_COOKIES 后启用自动 HTTPS
# docker compose --profile https up -d --build --wait
```

数据保存在 `radar_data` 命名卷，重建镜像不丢失配置和历史。SMTP 通过面板设置，不写入镜像。

## 首次使用

1. 登录，进入 **商家与机型**，选择候选或点击 **自定义监控**。
2. 核对检测地址、产品名称和规则，点击 **试运行规则**。试运行不会改变库存状态或发信。
3. 确认结果后勾选 **启用监控**。预设和导入配置默认暂停，避免把历史参考数据当成当前可购买产品。
4. 在 **通知与设置** 保存 SMTP 配置并启用邮件。使用邮箱服务商提供的 SMTP 授权码，然后发送测试邮件，在 **通知记录** 查看投递结果。
5. 默认只通知已确认的 **缺货 → 有货**。希望第一次检查就有货时也提醒，可在单机型设置中勾选 **首次确认有货也通知**。

面板每 15 秒同步一次；真正的库存检测默认每 120 秒执行。两者独立。价格是用户可编辑的展示备注，**不自动抓价、不换算汇率、不进行预算过滤**。这避免将历史促销价或 CAD 误当作当前 USD 月付价。

## 商家支持范围

| 商家          | 会话中的重点                                 | 当前接入方式                                                                                   |
| ------------- | -------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| VMISS         | US.LA.TRI Basic / Core，DC2 Basic / Core     | TRI 目录地址已配置；自动精确匹配产品名、提取 PID，再验证对应配置页。DC2 请填写当前目录或 PID。 |
| VMRack        | L3.VPS.DC2.2C2G.Base                         | 官方 pricing 表按完整机型定位单行；缺货标记优先于同行 Buy now。                                |
| DMIT          | LAX.Pro.WEE、LAX.EB.CORONA、LAX.AS3.Pro.TINY | WHMCS；需填当前官方产品 PID 或可解析目录，不猜活动链接。                                       |
| BandwagonHost | MegaBOX Pro、MiniBOX                         | WHMCS；历史特价需要当前活动的商品 PID。                                                        |
| ZgoCloud      | Los Angeles AMD Optimised 1G                 | HostBill HTML 单套餐区域或公开 JSON 字段；需要设置精确选择器。                                 |
| VIRCS         | CN2 GIA Lite                                 | 原域名访问时跳转到 NodeMach 公告，预设保持未配置，待用户核实新地址。                           |
| DigitalFyre   | LA 9950X 8C8G $10 历史活动                   | 活动地址需核实，支持 HTML / JSON；不能用常规 VPS-M8 库存替代。                                 |

**预设代表候选及规则模板，不代表已验证所有商家能从你的服务器访问。** 开发时部分官方站点返回 403、TLS 证书错误或超时；程序保留证书验证，不绕过验证码。实际可用性取决于部署服务器的出口、商家防护和页面结构。未填地址、目录下架、JavaScript 页面、登录或人机验证都会返回未知/失败；不会据此发送补货通知。

## 如何配置检测规则

### WHMCS

填写官方 `https://商家/cart.php?a=add&pid=实际编号` 和 **预期产品文字**。程序需要看到该产品名称和 `#frmConfigureProduct` 表单才确认有货。明确的缺货页面优先处理；跳转到其他 PID 或登录页不能视为有货。

也可填写 `.product` 卡片结构的 WHMCS 目录 URL，预期文字要和卡片标题精确一致。程序找到唯一名称后读取对应 PID 页面；两次请求间隔 5 秒。只有目录的 Order Now 按钮不会触发有货。如果模板不兼容，在 **从 WHMCS 目录读取机型** 中检查解析结果，或手动填写 PID。

### HTML

以 VMRack 为例：

```text
检测地址：https://www.vmrack.net/pricing
CSS 选择器：tr
产品名筛选：L3.VPS.DC2.2C2G.Base
预期产品文字：L3.VPS.DC2.2C2G.Base
有货标记：Buy now
缺货标记：Sold Out
```

规则必须只匹配一个区域。多个区域或零个区域均返回未知。负面标记优先；比如同一行既有 Sold Out 又有 Buy now，结果是缺货。移除 script、style、template、hidden 等非可见节点后进行文本判断。程序不运行浏览器 CSS/JS，因此使用行内 CSS 隐藏内容的特殊站点需要另选更精确区域或 JSON 接口。

### JSON

```json
{
  "provider": "Example",
  "name": "LAX Basic",
  "url": "https://example.com/public-stock.json",
  "adapter": "json",
  "json_path": "data.products.0.available",
  "in_stock": ["true"],
  "out_of_stock": ["false"],
  "interval": 120,
  "enabled": false
}
```

路径支持对象键及数组索引，字段值必须是标量。有货/缺货采用完整值匹配。字段不存在或结构变化返回未知。使用公开的只读接口；当前不支持携带商家登录 Cookie、Bearer Token 或执行页面 JavaScript。

## 调度与通知语义

- 有货和缺货都要连续确认，默认 2 次，可设 1–5 次。未知/失败会打断连续计数，但不会抹掉上次已确认库存。
- 一旦确认状态变化，事务内写入事件；只有有货转换且启用通知时才写邮件队列。每个事件至多生成一条通知。
- 重启后保留库存基线、检查时间、邮件队列；不会因重启重复生成补货通知。修改检测规则或切换启停会清空该监控基线，并取消旧规则的排队通知。
- SMTP 失败最多尝试 5 次并指数退避。尚未投递的补货邮件超过 1 小时、库存已确认变更或监控被停用时取消。
- SMTP 是 **至少一次投递**：极端情况下，服务器收信后应用在落库前退出，或部分收件人被拒收后重试，可能重复收到同一邮件。无法保证跨 SMTP 的严格 exactly-once。
- 首次确认时未启用 SMTP，之后再开启不会补发旧事件；可以开启首次有货通知并重新启用该监控获取新的基线。
- 失败后按 2 倍延长周期，最高约 1 小时，附加最多 10 秒抖动。成功识别后恢复配置周期。每个站点同时最多一个检查，完成后至少等待 5 秒。
- 单实例最多 500 个监控，4 个检查并发；小机型数量通常只需要数百 MB 内存。大量同站点机型会排队，实际间隔可能长于设置值。
- 检查保留 7 天且最多 20 万条；事件和通知保留 90 天。部署镜像日志限制为 3 × 10 MB。
- **只允许单个进程 / 单 Uvicorn worker 访问一个数据目录**。文件锁会拒绝重复实例。横向扩容需要额外的分布式调度设计。

## 安全与运维

密码使用 scrypt 哈希；会话使用 HttpOnly、SameSite Cookie，写接口校验 CSRF；SMTP 密码通过数据卷中的 `secret.key` 加密。该密钥和数据库应一起保护、一起备份。网页不依赖 CDN、在线字体或第三方脚本。

检测请求只允许 HTTP/HTTPS 80/443，拒绝本机、内网、元数据服务、保留和多播地址。每次跳转重新解析校验 DNS，并将已验证 IP 固定到实际连接，同时保持 TLS 主机名验证。跨域跳转和 HTTPS 降级返回未知，需手动核对新地址。SMTP 主机由管理员配置，允许自己的内网邮件中继；仅支持加密连接。

```bash
# 状态与日志
docker compose ps
docker compose logs --tail=100 -f radar

# 更新：把新代码同步到仓库后执行；沿用数据卷
bash deploy.sh

# 停止，保留数据
docker compose --profile https down
```

不要用 `down -v` 更新，`-v` 会删除持久化卷。`/healthz` 反映调度器进程心跳，不表示所有商家都能访问。查看面板中的未知/失败记录定位站点问题。

### 备份与恢复

直接 Python 部署可在线执行一致性备份：

```bash
python scripts/backup.py --data data --output backups/2026-09-06
```

Docker 部署可以先停止应用，再从容器复制整个数据目录（停机备份保证 WAL 一致）：

```bash
mkdir -p backups/2026-09-06
docker compose stop radar
docker compose cp radar:/data/. backups/2026-09-06/
docker compose start radar
```

恢复时停止应用，保存当前数据副本，将备份的 `radar.db` 和 `secret.key` 恢复到数据目录，移走旧的 `radar.db-wal` / `radar.db-shm`，确认容器用户 `10001:10001` 有读写权限后启动。不要将旧 WAL 与新数据库混用。卷备份可能包含初始密码文件，妥善保管。

## 本地开发

```bash
python -m venv .venv
source .venv/bin/activate
# Windows PowerShell: .venv/Scripts/Activate.ps1
pip install -r requirements-dev.txt
python -m app
```

访问 `http://127.0.0.1:8080`。未设置 `ADMIN_PASSWORD` 时，首次启动生成随机密码并写入 `data/initial-password.txt`。本地 Python 不自动加载 `.env`；需设置环境变量或使用生成密码。`.env` 由 Docker Compose 读取。

```bash
pytest -q
ruff check app tests scripts
node --check app/static/app.js
bash -n deploy.sh
```

测试不请求真实商家或发送真实邮件。覆盖产品区域匹配、缺货优先、WHMCS / JSON、403 / 验证页、连续确认、重启去重、邮件重试、规则编辑竞态、认证 / CSRF、配置导入、加密及 DNS 固定连接。CI 另外执行 Linux Docker 构建与 Compose 配置检查。

## 项目结构

```text
app/main.py        管理 API、认证与应用生命周期
app/engine.py      调度、库存状态机、持久化通知队列
app/detectors.py   HTML / WHMCS / JSON 检测
app/fetcher.py     限流外的网络抓取与 SSRF 防护
app/mailer.py      SMTP 发送
app/db.py          SQLite 表结构和连接
app/catalog.json   商家及机型预设
app/static/        中文管理面板
scripts/backup.py  一致性备份
tests/             自动化测试
```

## 参考与信息来源

- 产品状态列表、检查时间、订阅入口参考 [VPS值得买库存监控](https://stock.vpszdm.com/)。
- 商家分组和补货记录呈现参考 [HostMonit](https://stock.hostmonit.com/)。未依赖其非公开 API，也未复制其代码。
- 候选来自用户提供的「LA VPS 补货监控」会话；会话中的价格与配置作为历史参考，缺少确认的字段不编造。
- [VMISS TRI 官方目录](https://app.vmiss.com/store/us-los-angeles-tri)、[VMRack 官方定价](https://www.vmrack.net/pricing)、[DMIT](https://www.dmit.io/)、[ZgoCloud 客户中心](https://clients.zgovps.com/)、[DigitalFyre 常规产品](https://www.digitalfyre.com/vps/)。预设整理日期：2026-09-06。

MIT License.
