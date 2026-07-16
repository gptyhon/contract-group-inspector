# 飞书群巡检器 (Feishu Group Inspector)

自动拉飞书机器人进群 + Token 管理 + 授权过期提醒的常驻守护进程。

## 功能

- **自动拉机器人** — 轮询搜索指定关键词的飞书群，自动拉机器人入群
- **Token 自动续期** — 在后台自动刷新 access_token，无需手动操作
- **授权过期提醒** — refresh_token 即将过期时，发送飞书消息提醒
- **简单续期** — 收到提醒后，手动执行 `lark-cli auth login` 扫码并更新 config.json

## 快速开始

### 1. 下载

```bash
git clone <你的仓库地址>
cd feishu-group-inspector
```

### 2. 配置

复制并修改 `config.json`：

```json
{
  "app_id": "cli_你的飞书应用AppID",
  "app_secret": "你的飞书应用AppSecret",
  "bot_app_id": "cli_要拉入群的机器人AppID",

  "chat_search_keyword": "协商",
  "chat_search_interval_seconds": 300,

  "refresh_token": "",
  "user_open_id": "",
  "auth_warn_days": 2,

  "log_file": "inspector.log"
}
```

### 3. 一键部署

```bash
chmod +x deploy.sh
./deploy.sh
```

部署脚本会自动：
- 检查 Python3 环境
- 安装 lark-cli（扫码授权工具）
- 检查配置文件
- 创建 systemd 服务并启动
- 执行一次测试运行

### 4. 授权

首次运行需要扫码授权：

```bash
lark-cli auth login
```

扫码完成后，将获取到的 `refresh_token` 和你的 `user_open_id` 填入 `config.json`。

## 续期方式

当 refresh_token 即将过期时，服务会发飞书消息提醒你。

续期步骤：
1. 在服务器上执行 `lark-cli auth login`
2. 扫码授权
3. 将新的 `refresh_token` 填入 `config.json`
4. 重启服务：`sudo systemctl restart feishu-group-inspector`

## 测试命令

```bash
# 单次运行测试（不实际执行操作）
python3 main.py --once --dry-run

# 单次运行 + 详细日志
python3 main.py --once --verbose

# 单次运行 + 实际执行
python3 main.py --once

# 自定义配置
python3 main.py --config /path/to/config.json --once

# 常驻运行（前台）
python3 main.py --verbose
```

## 服务管理

```bash
sudo systemctl status feishu-group-inspector    # 查看状态
journalctl -u feishu-group-inspector -f         # 查看日志
sudo systemctl restart feishu-group-inspector   # 重启
sudo systemctl stop feishu-group-inspector      # 停止
```

## 依赖

- Python 3.8+
- lark-cli（部署脚本自动安装）
- 无其他 Python 外部依赖（纯标准库）
