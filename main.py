#!/usr/bin/env python3
"""
飞书群巡检器 - 常驻守护进程
功能：自动拉机器人进群 + Token 自动续期 + 授权过期提醒
"""

import argparse
import datetime
import json
import logging
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

# ─────────────────────────────────────────────
# 配置
# ─────────────────────────────────────────────
CONFIG_FILE = Path(__file__).parent / "config.json"
TOKEN_FILE = Path(__file__).parent / ".token_cache.json"

LARK_CLI = shutil_which = "lark-cli"

# ─────────────────────────────────────────────
# 日志
# ─────────────────────────────────────────────
def setup_logging(log_file, verbose):
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "%(asctime)s [%(levelname)s] %(message)s"

    handlers = [logging.StreamHandler()]
    if log_file:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))

    logging.basicConfig(level=level, format=fmt, handlers=handlers)
    return logging.getLogger("inspector")


# ─────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────
def extract_json(text):
    """从输出中提取第一个 JSON 对象/数组"""
    start = re.search(r"[\[{]", text)
    if not start:
        raise ValueError(f"未找到 JSON，前 300 字符: {text[:300]}")
    stack = []
    for i, ch in enumerate(text[start.start():]):
        if ch in "{[":
            stack.append(ch)
        elif ch == "}":
            if stack and stack[-1] == "{":
                stack.pop()
                if not stack:
                    return json.loads(text[start.start():start.start() + i + 1])
        elif ch == "]":
            if stack and stack[-1] == "[":
                stack.pop()
                if not stack:
                    return json.loads(text[start.start():start.start() + i + 1])
    raise ValueError("JSON 未闭合")


def run_cmd(cmd, timeout=120):
    """执行 shell 命令"""
    log = logging.getLogger("inspector")
    log.debug("  $ %s", cmd[:200])
    result = subprocess.run(
        cmd, shell=True, capture_output=True, text=True, timeout=timeout
    )
    if result.returncode != 0:
        detail = (result.stderr.strip() or result.stdout.strip())[:500]
        raise RuntimeError(f"exit={result.returncode} | {detail}")
    return result.stdout


# ─────────────────────────────────────────────
# Config / Token 管理
# ─────────────────────────────────────────────
def load_config():
    if not CONFIG_FILE.exists():
        print(f"[ERROR] 配置文件不存在: {CONFIG_FILE}")
        print(f"        请复制 config.json.example 并修改")
        sys.exit(1)
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_token_cache(access_token, refresh_token, expires_in):
    data = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at": time.time() + expires_in,
        "updated_at": datetime.datetime.now().isoformat(),
    }
    TOKEN_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def load_token_cache():
    if not TOKEN_FILE.exists():
        return None
    try:
        return json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


def refresh_access_token(config):
    """用 refresh_token 换新的 access_token + refresh_token"""
    log = logging.getLogger("inspector")
    log.info("  🔄 正在刷新 access_token ...")

    refresh_token = config.get("refresh_token") or ""
    if not refresh_token:
        log.warning("  ⚠️  没有 refresh_token，跳过自动续期")
        return False

    try:
        # 优先使用 token_cache 中的 refresh_token
        cache = load_token_cache()
        if cache and cache.get("refresh_token"):
            refresh_token = cache["refresh_token"]

        # 直接调用飞书 OAuth API 刷新
        data = json.dumps({
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "app_id": config["app_id"],
            "app_secret": config["app_secret"],
        })
        result = run_cmd(
            f'curl -s -X POST "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal" '
            f'-H "Content-Type: application/json" '
            f'-d {shlex.quote(data)}',
            timeout=15
        )
        body = json.loads(result)
        if body.get("code") != 0:
            log.error("  ❌ 刷新失败: %s", body.get("msg", "未知错误"))
            return False

        new_access = body.get("tenant_access_token", "")
        expires_in = body.get("expire", 7200)

        # 注意：tenant_access_token 没有 refresh_token，我们使用 app_id/app_secret 续期
        # 用户授权（user_access_token）需要用 refresh_token 续期
        # 这里同时尝试用 refresh_token 换 user_access_token

        # 用 refresh_token 换 user_access_token
        data2 = json.dumps({
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        })
        result2 = run_cmd(
            f'curl -s -X POST "https://open.feishu.cn/open-apis/authen/v1/oidc/refresh" '
            f'-H "Content-Type: application/json" '
            f'-H "Authorization: Bearer {new_access}" '
            f'-d {shlex.quote(data2)}',
            timeout=15
        )
        body2 = json.loads(result2)

        if body2.get("code") == 0:
            data2 = body2.get("data", {})
            new_user_token = data2.get("access_token", "")
            new_refresh = data2.get("refresh_token", refresh_token)
            new_expires = data2.get("expires_in", 7200)
            save_token_cache(new_user_token, new_refresh, new_expires)
            log.info("  ✅ access_token 已刷新")
            return True
        else:
            log.error("  ❌ 用户 token 刷新失败: %s", body2.get("msg", ""))
            return False

    except Exception as e:
        log.error("  ❌ 刷新 access_token 异常: %s", e)
        return False


def check_auth_expiry(config):
    """检查 refresh_token 剩余有效期"""
    log = logging.getLogger("inspector")

    try:
        # 从 token_cache 获取 refresh_token 的过期信息
        cache = load_token_cache()
        if not cache:
            log.debug("  没有 token_cache，跳过过期检测")
            return None

        # 尝试从 lark-cli 获取过期时间
        result = run_cmd("lark-cli auth status", timeout=10)
        data = extract_json(result)
        identities = data.get("identities", {})
        user_info = identities.get("user", {})
        expires_at_str = user_info.get("refreshExpiresAt", "")

        if not expires_at_str:
            return None

        expires_dt = datetime.datetime.fromisoformat(expires_at_str)
        now = datetime.datetime.now(expires_dt.tzinfo)
        delta = expires_dt - now
        days_left = delta.total_seconds() / 86400.0
        return days_left, expires_at_str

    except Exception as e:
        log.debug("  检测授权过期失败: %s", e)
        return None


def send_auth_warning(user_open_id, days_left, expires_at_str, config):
    """发送授权过期提醒消息"""
    log = logging.getLogger("inspector")

    now = datetime.datetime.now()
    date_str = now.strftime("%Y-%m-%d")

    message = (
        f"⚠️ 飞书授权即将过期\n\n"
        f"您的用户授权将在 {days_left:.1f} 天后过期\n"
        f"过期时间：{expires_at_str}\n\n"
        f"续期方式：\n"
        f"1. 打开 config.json\n"
        f"2. 执行 lark-cli auth login 重新扫码授权\n"
        f"3. 将新的 refresh_token 填入 config.json\n"
        f"4. 重启服务\n\n"
        f"发送于 {date_str} · 飞书群巡检器"
    )

    content = json.dumps({"text": message}, ensure_ascii=False)

    try:
        run_cmd(
            f'lark-cli im +messages-send --as bot '
            f'--user-id {user_open_id} '
            f'--msg-type text '
            f'--content {shlex.quote(content)}',
            timeout=15
        )
        log.info("  ✅ 已发送授权提醒到 %s", user_open_id)
        return True
    except Exception as e:
        log.error("  ❌ 发送授权提醒失败: %s", e)
        return False


# ─────────────────────────────────────────────
# 群巡检 + 拉机器人
# ─────────────────────────────────────────────
def scan_and_invite(config):
    """搜索关键词群，拉机器人进群"""
    log = logging.getLogger("inspector")
    keyword = config.get("chat_search_keyword", "协商")
    bot_app_id = config.get("bot_app_id", "")
    base_token = config.get("base_token", "")
    table_id = config.get("table_id", "")
    dry_run = config.get("_dry_run", False)

    log.info("  🔍 搜索关键词: '%s'", keyword)

    # 获取当前用户信息
    try:
        output = run_cmd("lark-cli whoami", timeout=10)
        data = extract_json(output)
        user_info = data.get("onBehalfOf", {})
        user_name = user_info.get("userName", "")
        user_open_id = user_info.get("openId", "")
        log.info("  当前用户: %s (%s)", user_name, user_open_id)
    except Exception as e:
        log.warning("  无法获取用户信息: %s", e)
        return

    # 搜索群
    page_token = None
    has_more = True
    total_new = 0

    while has_more:
        cmd = (
            f'lark-cli im +chat-search --as user '
            f'--query {shlex.quote(keyword)} --page-size 5 --format json'
        )
        if page_token:
            cmd += f' --page-token "{page_token}"'

        try:
            output = run_cmd(cmd, timeout=30)
            body = extract_json(output).get("data", {})
        except Exception as e:
            log.error("  搜索群失败: %s", e)
            break

        chats = body.get("chats", []) or []
        has_more = body.get("has_more", False)
        page_token = body.get("page_token", "")
        log.debug("  找到 %d 个群, has_more=%s", len(chats), has_more)

        for chat in chats:
            name = chat.get("name", "")
            chat_id = chat.get("chat_id", "")

            # 检查是否已在 Bitable
            if base_token and table_id:
                try:
                    # 查 Bitable 看看是否已有记录
                    list_cmd = (
                        f'lark-cli base +record-list --as user '
                        f'--base-token {base_token} --table-id {table_id} '
                        f'--limit 1 --offset 0 --format json'
                    )
                    # 简化：直接检查机器人是否已在群中
                except Exception:
                    pass

            # 检查机器人是否已在群中
            if bot_app_id:
                try:
                    members_out = run_cmd(
                        f'lark-cli im +chat-members-list --as user '
                        f'--chat-id {chat_id} --page-all --format json',
                        timeout=15
                    )
                    data = extract_json(members_out)
                    bots = data.get("data", {}).get("bots", [])
                    already_in = any(b.get("app_id") == bot_app_id for b in bots)

                    if already_in:
                        log.info("  ⏭️  机器人已在群: %s", name)
                        continue

                except Exception as e:
                    log.warning("  检查群成员失败: %s", name, e)
                    continue

                # 拉机器人入群
                if dry_run:
                    log.info("  🔄 [DRY RUN] 将拉机器人到: %s", name)
                else:
                    try:
                        data_json = json.dumps({"id_list": [bot_app_id]})
                        result = run_cmd(
                            f'lark-cli im chat.members create --as user '
                            f'--chat-id {chat_id} --member-id-type app_id '
                            f"--data '{data_json}' --succeed-type 1",
                            timeout=15
                        )
                        resp = extract_json(result)
                        invalid = resp.get("data", {}).get("invalid_id_list", [])
                        if bot_app_id not in invalid:
                            log.info("  ✅ 已拉机器人到群: %s", name)
                            total_new += 1
                        else:
                            log.warning("  ❌ 拉机器人失败: %s", name)
                    except Exception as e:
                        log.error("  ❌ 拉机器人异常: %s - %s", name, e)

        if not has_more:
            break

    log.info("  本次新增 %d 个群", total_new)


# ─────────────────────────────────────────────
# 主循环
# ─────────────────────────────────────────────
def main_loop(config, log):
    """常驻主循环"""
    interval = config.get("chat_search_interval_seconds", 300)
    user_open_id = config.get("user_open_id", "")
    auth_warn_days = config.get("auth_warn_days", 2)

    log.info("=" * 50)
    log.info("  飞书群巡检器 v2.0 已启动")
    log.info("  搜索间隔: %d 秒", interval)
    log.info("  授权提醒阈值: %d 天", auth_warn_days)
    log.info("=" * 50)

    last_scan_time = 0
    warned = False

    while True:
        now = time.time()

        # 1. Token 管理：自动续期
        refresh_access_token(config)

        # 2. 授权过期检测
        auth_result = check_auth_expiry(config)
        if auth_result:
            days_left, expires_at_str = auth_result
            log.debug("  授权剩余: %.1f 天 (到期: %s)", days_left, expires_at_str)

            if days_left < auth_warn_days and not warned:
                if user_open_id:
                    send_auth_warning(user_open_id, days_left, expires_at_str, config)
                warned = True
            elif days_left >= auth_warn_days:
                warned = False
        else:
            log.debug("  无法检测授权状态")

        # 3. 群巡检（按间隔执行）
        if now - last_scan_time >= interval:
            log.info("  --- 开始群巡检 ---")
            scan_and_invite(config)
            last_scan_time = now
            log.info("  --- 群巡检完成 ---")

        time.sleep(60)  # 每分钟检查一次


# ─────────────────────────────────────────────
# 入口
# ─────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="飞书群巡检器 - 常驻守护进程")
    parser.add_argument("--once", action="store_true", help="只运行一次后退出（测试用）")
    parser.add_argument("--dry-run", action="store_true", help="不实际执行操作，只打印（测试用）")
    parser.add_argument("--verbose", action="store_true", help="详细日志输出")
    parser.add_argument("--config", type=str, default=None, help="指定配置文件路径")
    parser.add_argument("--interval", type=int, default=None, help="覆盖 config 中的搜索间隔（秒）")
    args = parser.parse_args()

    # 加载配置
    if args.config:
        global CONFIG_FILE
        CONFIG_FILE = Path(args.config)

    config = load_config()
    log = setup_logging(config.get("log_file"), args.verbose or config.get("verbose", False))

    # 覆盖参数
    if args.interval:
        config["chat_search_interval_seconds"] = args.interval
    if args.dry_run:
        config["_dry_run"] = True

    if args.once:
        # 单次运行模式
        log.info("单次运行模式")
        refresh_access_token(config)

        auth_result = check_auth_expiry(config)
        if auth_result:
            days_left, expires_at_str = auth_result
            log.info("授权剩余: %.1f 天 (到期: %s)", days_left, expires_at_str)
            if days_left < config.get("auth_warn_days", 2):
                log.info("⚠️  授权即将过期，建议续期")

        scan_and_invite(config)
        log.info("单次运行完成")
        return

    # 常驻模式
    main_loop(config, log)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n服务已停止")
    except Exception as e:
        logging.getLogger("inspector").exception("服务异常退出: %s", e)
        sys.exit(1)
