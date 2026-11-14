"""命令行账号管理（不需要先启动 Web 服务）。

示例：
  python -m backend.app.db.user_cli list
  python -m backend.app.db.user_cli create zhangsan --password "Str0ng@Pass" --role operator
  python -m backend.app.db.user_cli passwd zhangsan --password "New@Pass123"
  python -m backend.app.db.user_cli deactivate zhangsan
  python -m backend.app.db.user_cli activate zhangsan

未传 --password 时会交互式隐藏输入（适合人工操作）。
"""
from __future__ import annotations

import argparse
import getpass
import sys

from backend.app.core import security as sec
from backend.app.db.session import SessionLocal, init_database
from backend.app.repositories import user_repo as repo
from backend.app.schemas.auth import UserCreateIn, UserUpdateIn
from backend.app.services import auth_service as svc


def _ask_password(label: str) -> str:
    if not sys.stdin.isatty():
        raise SystemExit("非交互终端请显式传入 --password")
    value = getpass.getpass(f"{label}: ")
    if not value:
        raise SystemExit("密码不能为空")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="关联交易发票处理台 · 账号管理")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="列出所有账号")

    p_create = sub.add_parser("create", help="新建账号")
    p_create.add_argument("username")
    p_create.add_argument("--password")
    p_create.add_argument("--display-name", default="")
    p_create.add_argument("--role", default="operator", choices=["admin", "operator", "viewer"])

    p_passwd = sub.add_parser("passwd", help="重置账号密码并撤销其全部会话")
    p_passwd.add_argument("username")
    p_passwd.add_argument("--password")

    for name in ("activate", "deactivate"):
        p = sub.add_parser(name, help=("启用" if name == "activate" else "停用") + "账号")
        p.add_argument("username")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    init_database()
    with SessionLocal() as db:
        if args.command == "list":
            rows = repo.list_users(db)
            print(f"{'ID':<4} {'用户名':<20} {'显示名':<16} {'角色':<10} {'状态':<8} 最近登录")
            for row in rows:
                print(f"{row.id:<4} {row.username:<20} {(row.display_name or '—'):<16} "
                      f"{row.role:<10} {'启用' if row.is_active else '停用':<8} "
                      f"{row.last_login_at or '—'}")
            print(f"共 {len(rows)} 个账号")
            return

        if args.command == "create":
            password = args.password or _ask_password("新用户密码（至少 8 位）")
            row = svc.create_user(db, UserCreateIn(
                username=args.username, password=password,
                display_name=args.display_name, role=args.role,
            ))
            print(f"已创建账号 {row.username}（id={row.id}, role={row.role}）")
            return

        username = sec.validate_username(args.username)
        row = repo.get_by_username(db, username)
        if row is None:
            raise SystemExit(f"账号不存在: {username}")

        if args.command == "passwd":
            password = args.password or _ask_password("新密码（至少 8 位）")
            svc.update_user(db, row.id, UserUpdateIn(password=password), operator="cli")
            print(f"已重置 {username} 的密码，并撤销其全部登录会话")
        elif args.command in ("activate", "deactivate"):
            active = args.command == "activate"
            svc.update_user(db, row.id, UserUpdateIn(is_active=active), operator="cli")
            print(f"账号 {username} 已{'启用' if active else '停用'}")


if __name__ == "__main__":
    main()
