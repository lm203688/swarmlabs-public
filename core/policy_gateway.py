"""
Policy Gateway - Deterministic Control Plane for High-Risk Operations
Inspired by MergePilot's Policy Gateway architecture

Features:
- ALLOW / DENY / ERROR decisions for all tool calls
- L0/L1/L2 risk-level gating
- Audit trail with structured facts
- fail-closed design principle
- CAS (Compare-And-Swap) for revision drift detection
"""

from enum import Enum
from typing import Dict, Any, List, Optional, Callable
import asyncio
import json
import time
import hashlib
from dataclasses import dataclass, field


class RiskLevel(Enum):
    L0 = "L0"  # Low risk, auto-approve
    L1 = "L1"  # Medium risk, auto-approve with logging
    L2 = "L2"  # High risk, requires human approval


class PolicyDecision(Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    ERROR = "ERROR"
    PENDING = "PENDING"


class AuditEventType(Enum):
    DECISION = "decision"
    EXECUTION = "execution"
    ROLLBACK = "rollback"
    APPROVAL = "approval"
    DENIAL = "denial"
    ERROR = "error"


@dataclass
class PolicyRule:
    rule_id: str
    name: str
    description: str
    risk_level: RiskLevel
    conditions: Dict[str, Any]
    action: str  # "allow", "deny", "require_approval"
    priority: int = 0
    enabled: bool = True


@dataclass
class AuditEvent:
    event_id: str
    event_type: AuditEventType
    timestamp: float
    operation: str
    risk_level: RiskLevel
    decision: PolicyDecision
    actor: str
    details: Dict[str, Any] = field(default_factory=dict)
    parent_event_id: Optional[str] = None


@dataclass
class ApprovalTicket:
    ticket_id: str
    operation: str
    risk_level: RiskLevel
    requested_at: float
    expires_at: float
    approved: bool = False
    approved_by: Optional[str] = None
    approved_at: Optional[float] = None
    sha_verified: Optional[str] = None


class PolicyGateway:
    """
    确定性控制面
    所有高风险操作必须经过Policy Gateway
    不确定时fail-closed
    """
    
    def __init__(self, api_key: Optional[str] = None,
                 default_decision: Optional[PolicyDecision] = None):
        """确定性控制面。

        default_decision 控制「规则未命中 / 路径未命中受保护前缀」时的兜底行为。
        诚实性与安全边界（P0 修复 2026-11-03）：默认 DENY —— 不确定时 fail-closed，
        与 docstring 宣称的 "fail-closed design principle" 一致，修复历史 fail-open 口子。
        旧代码默认 ALLOW（"Default: allow with logging"），任何调用方如需恢复旧行为，
        必须显式传 PolicyDecision.ALLOW，并在审计日志中留下记录。
        """
        self.api_key = api_key
        self.rules: List[PolicyRule] = []
        self.audit_log: List[AuditEvent] = []
        self.pending_approvals: Dict[str, ApprovalTicket] = {}
        self.protected_prefixes: List[str] = []
        # fail-closed：默认 DENY，不确定即阻断（2026-11-03 P0 修复）
        self.default_decision = default_decision or PolicyDecision.DENY
        self._initialize_default_rules()
    
    def _initialize_default_rules(self):
        """初始化默认规则"""
        self.rules = [
            PolicyRule(
                rule_id="R001",
                name="file_delete_protection",
                description="Protect against accidental file deletion",
                risk_level=RiskLevel.L2,
                conditions={"action_type": "delete", "path_pattern": ".*"},
                action="require_approval",
                priority=100
            ),
            PolicyRule(
                rule_id="R002",
                name="git_push_protection",
                description="Protect against unauthorized git push",
                risk_level=RiskLevel.L2,
                conditions={"action_type": "git_push", "branch_pattern": "main|master|production"},
                action="require_approval",
                priority=90
            ),
            PolicyRule(
                rule_id="R003",
                name="api_key_access",
                description="Protect API key access",
                risk_level=RiskLevel.L1,
                conditions={"action_type": "read", "content_pattern": "api_key|secret|token"},
                action="allow",
                priority=80
            ),
            PolicyRule(
                rule_id="R004",
                name="code_execution",
                description="Code execution requires verification",
                risk_level=RiskLevel.L1,
                conditions={"action_type": "execute", "target": "code"},
                action="allow",
                priority=70
            ),
            PolicyRule(
                rule_id="R005",
                name="database_write",
                description="Database write operations require approval",
                risk_level=RiskLevel.L2,
                conditions={"action_type": "write", "target": "database"},
                action="require_approval",
                priority=85
            )
        ]
        
        self.protected_prefixes = [
            "/etc/",
            "/usr/",
            "/var/",
            "~/.ssh/",
            "~/.aws/",
            "~/.config/"
        ]
    
    def add_rule(self, rule: PolicyRule):
        """添加规则"""
        self.rules.append(rule)
        self.rules.sort(key=lambda r: r.priority, reverse=True)
    
    def evaluate(self, operation: str, context: Dict[str, Any]) -> PolicyDecision:
        """评估操作是否允许"""
        # Check protected prefixes
        path = context.get("path", "")
        for prefix in self.protected_prefixes:
            if path.startswith(prefix):
                self._log_audit(
                    operation=operation,
                    risk_level=RiskLevel.L2,
                    decision=PolicyDecision.DENY,
                    actor=context.get("actor", "unknown"),
                    details={"reason": f"Protected path prefix: {prefix}"}
                )
                return PolicyDecision.DENY
        
        # Evaluate rules
        for rule in self.rules:
            if not rule.enabled:
                continue
            if self._rule_matches(rule, operation, context):
                if rule.action == "deny":
                    self._log_audit(
                        operation=operation,
                        risk_level=rule.risk_level,
                        decision=PolicyDecision.DENY,
                        actor=context.get("actor", "unknown"),
                        details={"rule": rule.rule_id, "reason": rule.description}
                    )
                    return PolicyDecision.DENY
                elif rule.action == "require_approval":
                    if rule.risk_level == RiskLevel.L2:
                        # Need human approval
                        ticket = self._create_approval_ticket(operation, rule.risk_level)
                        self._log_audit(
                            operation=operation,
                            risk_level=rule.risk_level,
                            decision=PolicyDecision.PENDING,
                            actor=context.get("actor", "unknown"),
                            details={"ticket_id": ticket.ticket_id}
                        )
                        return PolicyDecision.PENDING
                    else:
                        self._log_audit(
                            operation=operation,
                            risk_level=rule.risk_level,
                            decision=PolicyDecision.ALLOW,
                            actor=context.get("actor", "unknown"),
                            details={"rule": rule.rule_id}
                        )
                        return PolicyDecision.ALLOW
        
        # Default: fail-closed —— 规则未命中、路径未命中受保护前缀时，不确定即 DENY。
        # 2026-11-03 P0 修复：原为 "Default: allow with logging"（fail-open），
        # 与 docstring 宣称的 fail-closed 原则矛盾，构成确定性控制面的安全口子。
        self._log_audit(
            operation=operation,
            risk_level=RiskLevel.L0,
            decision=self.default_decision,
            actor=context.get("actor", "unknown"),
            details={"default": True, "fail_closed": self.default_decision == PolicyDecision.DENY}
        )
        return self.default_decision
    
    def _rule_matches(self, rule: PolicyRule, operation: str, context: Dict[str, Any]) -> bool:
        """检查规则是否匹配"""
        conditions = rule.conditions
        
        if "action_type" in conditions and conditions["action_type"] != context.get("action_type"):
            return False
        
        if "path_pattern" in conditions:
            import re
            path = context.get("path", "")
            if not re.match(conditions["path_pattern"], path):
                return False
        
        if "branch_pattern" in conditions:
            import re
            branch = context.get("branch", "")
            if not re.match(conditions["branch_pattern"], branch):
                return False
        
        if "content_pattern" in conditions:
            import re
            content = context.get("content", "")
            if re.search(conditions["content_pattern"], content):
                return True
            return False
        
        return True
    
    def _create_approval_ticket(self, operation: str, risk_level: RiskLevel) -> ApprovalTicket:
        """创建审批工单"""
        ticket_id = hashlib.md5(f"{operation}{time.time()}".encode()).hexdigest()[:8]
        ticket = ApprovalTicket(
            ticket_id=ticket_id,
            operation=operation,
            risk_level=risk_level,
            requested_at=time.time(),
            expires_at=time.time() + 3600  # 1 hour expiry
        )
        self.pending_approvals[ticket_id] = ticket
        return ticket
    
    def approve(self, ticket_id: str, approver: str, sha: Optional[str] = None) -> bool:
        """审批操作"""
        if ticket_id not in self.pending_approvals:
            return False
        
        ticket = self.pending_approvals[ticket_id]
        if time.time() > ticket.expires_at:
            del self.pending_approvals[ticket_id]
            return False
        
        ticket.approved = True
        ticket.approved_by = approver
        ticket.approved_at = time.time()
        ticket.sha_verified = sha
        
        self._log_audit(
            operation=ticket.operation,
            risk_level=ticket.risk_level,
            decision=PolicyDecision.ALLOW,
            actor=approver,
            details={"ticket_id": ticket_id, "sha": sha}
        )
        
        return True
    
    def deny(self, ticket_id: str, denier: str, reason: str = "") -> bool:
        """拒绝操作"""
        if ticket_id not in self.pending_approvals:
            return False
        
        ticket = self.pending_approvals[ticket_id]
        
        self._log_audit(
            operation=ticket.operation,
            risk_level=ticket.risk_level,
            decision=PolicyDecision.DENY,
            actor=denier,
            details={"ticket_id": ticket_id, "reason": reason}
        )
        
        del self.pending_approvals[ticket_id]
        return True
    
    def verify_revision(self, approved_sha: str, current_sha: str) -> bool:
        """验证revision是否漂移"""
        if approved_sha != current_sha:
            self._log_audit(
                operation="revision_check",
                risk_level=RiskLevel.L2,
                decision=PolicyDecision.DENY,
                actor="system",
                details={
                    "approved_sha": approved_sha,
                    "current_sha": current_sha,
                    "reason": "REVISION_DRIFT"
                }
            )
            return False
        return True
    
    def _log_audit(self, operation: str, risk_level: RiskLevel,
                   decision: PolicyDecision, actor: str,
                   details: Dict[str, Any]):
        """记录审计日志"""
        event = AuditEvent(
            event_id=hashlib.md5(f"{operation}{time.time()}".encode()).hexdigest()[:12],
            event_type=AuditEventType.DECISION if decision != PolicyDecision.PENDING else AuditEventType.DENIAL,
            timestamp=time.time(),
            operation=operation,
            risk_level=risk_level,
            decision=decision,
            actor=actor,
            details=details
        )
        self.audit_log.append(event)
    
    def get_audit_trail(self, limit: int = 50) -> List[Dict[str, Any]]:
        """获取审计轨迹"""
        recent = self.audit_log[-limit:]
        return [
            {
                "event_id": e.event_id,
                "event_type": e.event_type.value,
                "timestamp": e.timestamp,
                "operation": e.operation,
                "risk_level": e.risk_level.value,
                "decision": e.decision.value,
                "actor": e.actor,
                "details": e.details
            }
            for e in recent
        ]
    
    def get_statistics(self) -> Dict[str, Any]:
        """获取统计信息"""
        total = len(self.audit_log)
        decisions = {}
        for event in self.audit_log:
            d = event.decision.value
            decisions[d] = decisions.get(d, 0) + 1
        
        return {
            "total_events": total,
            "decisions": decisions,
            "pending_approvals": len(self.pending_approvals),
            "active_rules": sum(1 for r in self.rules if r.enabled)
        }


_policy_gateway_instance = None

def get_policy_gateway(api_key: Optional[str] = None) -> PolicyGateway:
    global _policy_gateway_instance
    if _policy_gateway_instance is None:
        _policy_gateway_instance = PolicyGateway(api_key)
    return _policy_gateway_instance
