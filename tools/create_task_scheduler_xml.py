"""產生 Windows Task Scheduler XML；預設只寫檔，不註冊或修改系統。"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from xml.sax.saxutils import escape


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="產生 MeowKeeper+ 無人登入背景啟動的 Task Scheduler XML（只寫檔）。"
    )
    parser.add_argument("--root", type=Path, required=True, help="可攜式包根目錄。")
    parser.add_argument(
        "--data-root",
        type=Path,
        help="設定、SQLite、事件與快取資料根目錄；省略時使用可攜包相對路徑。",
    )
    parser.add_argument("--output", type=Path, required=True, help="輸出的 XML 路徑。")
    parser.add_argument("--task-name", default="MeowKeeperPlus", help="Task Scheduler 顯示名稱。")
    parser.add_argument(
        "--source-env",
        default="MEOWKEEPER_MEDIA_SOURCE_URL",
        help="來源 URL 環境變數名稱；不把 RTSP 密碼寫入 XML。",
    )
    parser.add_argument(
        "--role",
        choices=("core", "user_core", "tray"),
        default="core",
        help=(
            "core 在開機且可無人登入執行；user_core 與 tray 在目前使用者登入後啟動。"
        ),
    )
    parser.add_argument(
        "--user-id",
        help="user_core／tray 的 Windows 使用者 SID；安裝器應明確提供。",
    )
    return parser


def create_xml(
    root: Path,
    *,
    task_name: str,
    source_env: str,
    role: str = "core",
    data_root: Path | None = None,
    user_id: str | None = None,
) -> str:
    if not root.is_dir():
        raise ValueError(f"找不到可攜式包根目錄：{root}")
    if not task_name.strip() or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", source_env):
        raise ValueError("Task 名稱不可為空白，來源環境變數須為有效名稱。")
    if role not in {"core", "user_core", "tray"}:
        raise ValueError("Task role 必須是 core、user_core 或 tray。")
    if user_id is not None and not re.fullmatch(r"S-\d+(?:-\d+)+", user_id):
        raise ValueError("Windows 使用者 SID 格式無效。")
    root_text = str(root.resolve())
    command = str(root.resolve() / "runtime" / "python" / ("pythonw.exe" if role == "tray" else "python.exe"))
    if role in {"user_core", "tray"}:
        trigger = "<LogonTrigger><Enabled>true</Enabled></LogonTrigger>"
        logon_type = "<LogonType>InteractiveToken</LogonType>"
    if role == "tray":
        arguments = f'"{root.resolve() / "tools" / "run_tray.py"}" --background'
        description = "Tray 操作入口；退出 Tray 不會停止背景核心"
    else:
        if role == "core":
            trigger = "<BootTrigger><Enabled>true</Enabled></BootTrigger>"
            # Task Scheduler infers SYSTEM from its SID. Its XML parser rejects
            # an explicit ServiceAccount LogonType for this principal.
            logon_type = ""
            user_id = "<UserId>S-1-5-18</UserId>"
        arguments = (
            f'"{root.resolve() / "tools" / "run_host.py"}" '
            f"--no-open-browser --source-url-env {source_env} --analyzer auto"
        )
        if data_root is not None:
            state = data_root.resolve()
            arguments += (
                f' --dashboard-config "{state / "config.json"}"'
                f' --database-path "{state / "meowkeeper.db"}"'
                f' --event-directory "{state / "events"}"'
                f' --ring-directory "{state / "cache" / "ring"}"'
                f' --preview-directory "{state / "cache" / "preview"}"'
                f' --admin-storage-path "{state / "web_admin.json"}"'
                f' --lock-root "{state.parent}"'
            )
        description = "24H 背景監控與 Web Dashboard Host"
    if role in {"user_core", "tray"}:
        user_id_xml = f"<UserId>{escape(user_id)}</UserId>" if user_id else ""
    else:
        user_id_xml = user_id
    if role == "tray" and data_root is not None:
        arguments += f' --lock-root "{data_root.resolve().parent}"'
    return f'''<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>{escape(task_name)}；MeowKeeper+ {escape(description)}</Description></RegistrationInfo>
  <Triggers>{trigger}</Triggers>
  <Principals><Principal id="Author">{user_id_xml}{logon_type}<RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy><DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries><StopIfGoingOnBatteries>false</StopIfGoingOnBatteries><AllowHardTerminate>true</AllowHardTerminate><StartWhenAvailable>true</StartWhenAvailable><ExecutionTimeLimit>PT0S</ExecutionTimeLimit><RestartOnFailure><Interval>PT1M</Interval><Count>3</Count></RestartOnFailure></Settings>
  <Actions Context="Author"><Exec><Command>{escape(command)}</Command><Arguments>{escape(arguments)}</Arguments><WorkingDirectory>{escape(root_text)}</WorkingDirectory></Exec></Actions>
</Task>
'''


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        xml = create_xml(
            args.root,
            task_name=args.task_name,
            source_env=args.source_env,
            role=args.role,
            data_root=args.data_root,
            user_id=args.user_id,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(xml, encoding="utf-16")
    except (OSError, ValueError) as exc:
        print(f"Task Scheduler XML 產生失敗：{exc}", file=sys.stderr)
        return 2
    print(f"已產生 XML：{args.output}")
    print("請由管理者明確執行 schtasks /Create /XML；本工具未自行註冊工作。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
