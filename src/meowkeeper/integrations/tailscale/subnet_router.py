"""Subnet Router 只提供明確 CIDR 的安全計畫，不修改 ACL 或路由。"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass


@dataclass(frozen=True)
class SubnetRoutePlan:
    status: str
    cidr: str | None
    requires_admin_approval: bool = True
    enabled: bool = False
    message_zh_tw: str = ""


def plan_subnet_route(cidr: str | None, *, enable: bool = False, confirm: bool = False) -> SubnetRoutePlan:
    if not cidr:
        return SubnetRoutePlan("not_checked", None, message_zh_tw="尚未指定要分享的 CIDR。")
    try:
        network = ipaddress.ip_network(cidr, strict=False)
    except ValueError:
        return SubnetRoutePlan("invalid", None, message_zh_tw="CIDR 格式不正確。")
    if network.version != 4 or network.prefixlen < 24:
        return SubnetRoutePlan("rejected", str(network), message_zh_tw="預設拒絕過大的家庭網段；請只指定必要的窄網段。")
    if enable and not confirm:
        return SubnetRoutePlan("confirmation_required", str(network), message_zh_tw="啟用路由需使用者確認並由管理頁批准。")
    return SubnetRoutePlan("ready_to_apply" if enable else "ready_to_disable", str(network), enabled=enable, message_zh_tw="不會自動修改 Tailscale ACL。")
