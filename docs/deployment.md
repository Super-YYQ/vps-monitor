# 部署、升级与备份

[返回 README](../README.md) · [完整设置说明](configuration.md) · [邮箱配置](email/README.md)

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

Linux / macOS，在仓库根目录执行：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m app
```

Windows PowerShell，在仓库根目录执行，无需修改脚本执行策略：

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-dev.txt
.venv/Scripts/python.exe -m app
```

迁移原生 Python 项目时先停止服务，完整保留 `data` 中的数据库和密钥；虚拟环境包含绝对路径，应在新位置重新创建并安装依赖。Docker 迁移还需要单独备份并恢复数据卷，仅复制仓库不会复制命名卷。

访问 `http://127.0.0.1:8080`。未设置 `ADMIN_PASSWORD` 时，首次启动生成随机密码并写入 `data/initial-password.txt`。本地 Python 不自动加载 `.env`；需设置环境变量或使用生成密码。`.env` 由 Docker Compose 读取。

激活虚拟环境后执行检查（也可直接使用虚拟环境中的 Python）：

```bash
python -m pytest -q
python -m ruff check app tests scripts
node --check app/static/app.js
bash -n deploy.sh
```

测试不请求真实商家或发送真实邮件。覆盖产品区域匹配、缺货优先、WHMCS / JSON、403 / 验证页、连续确认、重启去重、邮件重试、规则编辑竞态、认证 / CSRF、配置导入、加密及 DNS 固定连接。CI 另外执行 Linux Docker 构建与 Compose 配置检查。
