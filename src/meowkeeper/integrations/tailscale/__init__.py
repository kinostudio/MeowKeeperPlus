"""官方 Tailscale Windows Client 的唯讀診斷與明確確認計畫。"""

from .client import TailscaleClient
from .models import TailscaleStatus
from .subnet_router import SubnetRoutePlan, plan_subnet_route

__all__ = ["SubnetRoutePlan", "TailscaleClient", "TailscaleStatus", "plan_subnet_route"]
