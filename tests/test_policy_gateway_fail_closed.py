"""P0 修复回归测试：policy_gateway 默认分支必须 fail-closed（DENY）。

背景（2026-11-03 专项体检 · 技术专家 + SwarmLabs 独立核实）：
- 原实现 evaluate() 默认分支为 "Default: allow with logging" → 返回 ALLOW，
  与 docstring 宣称的 "fail-closed design principle" 直接矛盾。
- 修复后默认决策为 DENY，任何未命中规则 / 未命中受保护前缀的操作默认阻断。
- 提供 default_decision 参数以显式恢复旧行为（必须显式传参，审计留痕）。
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.policy_gateway import PolicyGateway, PolicyDecision


def test_default_decision_is_deny_fail_closed():
    """未命中任何规则且未命中受保护前缀 → 默认 DENY（fail-closed）"""
    gw = PolicyGateway()
    # 一个完全未知的操作，不匹配任何规则，路径也不在受保护前缀内
    decision = gw.evaluate("unknown_operation", {"path": "/tmp/somewhere"})
    assert decision == PolicyDecision.DENY


def test_unmatched_path_deny():
    """路径未命中受保护前缀 → 默认 DENY（fail-closed）"""
    gw = PolicyGateway()
    decision = gw.evaluate("read_file", {"path": "/data/result.json", "action_type": "read"})
    assert decision == PolicyDecision.DENY


def test_protected_prefix_still_deny():
    """受保护路径前缀始终 DENY，不因默认决策改变"""
    gw = PolicyGateway()
    decision = gw.evaluate("write", {"path": "/etc/passwd", "action_type": "write"})
    assert decision == PolicyDecision.DENY


def test_explicit_rule_deny_wins():
    """显式规则 deny 仍优先生效"""
    gw = PolicyGateway()
    decision = gw.evaluate("delete", {"path": "/data/x", "action_type": "delete"})
    # R001 file_delete_protection → require_approval → PENDING（L2）
    assert decision == PolicyDecision.PENDING


def test_default_decision_parameter_restores_allow():
    """显式传 default_decision=ALLOW 可恢复旧行为（兼容通道），但需显式声明"""
    gw = PolicyGateway(default_decision=PolicyDecision.ALLOW)
    decision = gw.evaluate("unknown_operation", {"path": "/tmp/somewhere"})
    assert decision == PolicyDecision.ALLOW


def test_audit_trail_records_fail_closed():
    """默认 DENY 决策必须写入审计日志，且带 fail_closed 标记"""
    gw = PolicyGateway()
    gw.evaluate("unknown_operation", {"path": "/tmp/somewhere", "actor": "test_actor"})
    trail = gw.get_audit_trail(limit=10)
    assert trail, "审计日志不应为空"
    last = trail[-1]
    assert last["decision"] == "DENY"
    assert last["details"].get("fail_closed") is True
    assert last["actor"] == "test_actor"
