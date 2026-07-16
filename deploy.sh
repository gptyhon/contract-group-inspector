#!/usr/bin/env bash
# ============================================
# 飞书群巡检器 - 一键部署脚本
# ============================================
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_NAME="feishu-group-inspector"
SERVICE_NAME="feishu-group-inspector"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# ── 检查 Python3 ──
check_python() {
    if ! command -v python3 &>/dev/null; then
        log_error "未找到 python3，请先安装 Python 3.8+"
        exit 1
    fi
    pyver=$(python3 --version 2>&1)
    log_info "Python: $pyver"
}

# ── 检查/安装 lark-cli ──
check_lark_cli() {
    if command -v lark-cli &>/dev/null; then
        log_info "lark-cli 已安装: $(lark-cli --version 2>&1)"
        return
    fi

    log_info "正在安装 lark-cli ..."

    # 检测系统架构
    ARCH=$(uname -m)
    case "$ARCH" in
        x86_64)  BIN="lark-cli_linux_amd64" ;;
        aarch64) BIN="lark-cli_linux_arm64" ;;
        *)       log_error "不支持的架构: $ARCH"; exit 1 ;;
    esac

    # 下载最新版
    LATEST_URL="https://github.com/larksuite/lark-cli/releases/latest/download/${BIN}.tar.gz"
    TMP_DIR=$(mktemp -d)
    cd "$TMP_DIR"

    if command -v curl &>/dev/null; then
        curl -sL "$LATEST_URL" -o lark-cli.tar.gz
    elif command -v wget &>/dev/null; then
        wget -q "$LATEST_URL" -O lark-cli.tar.gz
    else
        log_error "需要 curl 或 wget"
        exit 1
    fi

    tar xzf lark-cli.tar.gz
    chmod +x lark-cli
    sudo mv lark-cli /usr/local/bin/
    rm -rf "$TMP_DIR"

    log_info "lark-cli 安装完成"
}

# ── 检查配置文件 ──
check_config() {
    if [ ! -f "$APP_DIR/config.json" ]; then
        log_error "config.json 不存在！"
        log_info "请复制 config.json.example 为 config.json 并填写配置"
        exit 1
    fi

    # 检查必填字段
    if grep -q '"app_id": "cli_' "$APP_DIR/config.json" 2>/dev/null; then
        log_info "config.json 存在，app_id 已配置"
    else
        log_warn "config.json 中的 app_id 似乎未正确填写，请检查"
    fi
}

# ── 检查 lark-cli 授权状态 ──
check_auth() {
    log_info "检查 lark-cli 授权状态..."

    if lark-cli auth status &>/dev/null; then
        log_info "lark-cli 已授权"
    else
        log_warn "lark-cli 未授权，请运行: lark-cli auth login"
        log_warn "扫码授权后重新运行本脚本"
    fi
}

# ── 安装 systemd 服务 ──
install_service() {
    log_info "安装 systemd 服务..."

    # 生成 service 文件
    sudo tee "$SERVICE_FILE" > /dev/null <<EOF
[Unit]
Description=飞书群巡检器 - 自动拉机器人进群 + Token 管理
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$(whoami)
WorkingDirectory=$APP_DIR
ExecStart=$(which python3) $APP_DIR/main.py
Restart=always
RestartSec=30
StandardOutput=journal
StandardError=journal
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

    sudo systemctl daemon-reload
    sudo systemctl enable "$SERVICE_NAME"
    sudo systemctl restart "$SERVICE_NAME"

    log_info "服务已启动: $SERVICE_NAME"
    log_info "查看日志: journalctl -u $SERVICE_NAME -f"
}

# ── 测试运行 ──
test_run() {
    log_info "执行测试运行（--once --dry-run）..."
    cd "$APP_DIR"
    python3 main.py --once --dry-run
    echo ""
    log_info "测试完成。如果一切正常，服务已在后台运行。"
}

# ═══════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════
echo ""
echo "  ╔══════════════════════════════════╗"
echo "  ║   飞书群巡检器 - 一键部署脚本    ║"
echo "  ╚══════════════════════════════════╝"
echo ""

check_python
check_lark_cli
check_config
check_auth
install_service
test_run

echo ""
log_info "部署完成！"
echo ""
echo "  常用命令:"
echo "    sudo systemctl status $SERVICE_NAME    # 查看服务状态"
echo "    journalctl -u $SERVICE_NAME -f         # 查看实时日志"
echo "    sudo systemctl restart $SERVICE_NAME   # 重启服务"
echo "    python3 main.py --once                 # 单次运行测试"
echo "    python3 main.py --once --verbose       # 详细测试"
echo ""
