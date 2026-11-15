"""权限边界、审批闸门与回滚。

为什么必要
----------
科学实验闭环里最危险的动作不是"算错"，而是**把错的东西写进持久化的知识库**
（这里是 data/validation_store/*.json——跨天累积的真实论文数据点）。一旦被
污染，后续所有天的验证都建立在脏数据上，且很难察觉。

因此本模块做三件事：
1. **静态边界**：每个角色能调哪些 Skill、能否写、能否触网，在 policy.yaml 声明。
2. **动态闸门**：写操作/高风险操作必须过 `gate()`；未获批准 → 返回 blocked，
   任务不失败但动作不执行（degrade，不是 crash），并留下 pending 审计。
3. **可回滚**：批准执行前先对目标文件做快照，写入 run_dir/rollback/，
   `run.py --rollback <run_id>` 可一键还原。
"""

from __future__ import annotations

import json
import os
import shutil
import time
from typing import Any, Dict

from .contracts import Decision


class Policy:
    def __init__(self, cfg: Dict[str, Any], run_dir: str, tracer,
                 approve_all: bool = False, approvals: Dict[str, bool] = None):
        self.cfg = cfg or {}
        self.run_dir = run_dir
        self.tracer = tracer
        self.approve_all = approve_all
        self.approvals = approvals or {}
        self.rollback_dir = os.path.join(run_dir, "rollback")

    # -- 静态边界 ----------------------------------------------------------
    def role_conf(self, role: str) -> Dict[str, Any]:
        return (self.cfg.get("roles") or {}).get(role, {})

    def skill_allowed_for_role(self, role: str, skill_name: str) -> bool:
        rc = self.role_conf(role)
        allow = rc.get("allow_skills")
        if allow is None:
            return True
        return skill_name in allow

    # -- 动态闸门 ----------------------------------------------------------
    def gate(self, actor: str, skill_name: str, side_effect: str, risk: str,
             scope: str = "run_local", detail: Dict[str, Any] = None) -> Decision:
        detail = detail or {}
        rc = self.role_conf(actor)

        if not self.skill_allowed_for_role(actor, skill_name):
            d = Decision(False, f"角色 {actor} 未被授权调用 Skill «{skill_name}»")
            self.tracer.audit("policy_check", actor, skill_name, "deny",
                              {**detail, "reason": d.reason})
            return d

        if side_effect == "external":
            # 外呼权限有两级：角色级开关（allow_external）与**逐 Skill 白名单**
            # （allow_external_skills）。后者让"需要外呼的单一能力"可被最小化
            # 授权，而不必把角色的整条外呼通道打开——本地模型顾问就是这种情形。
            allow_ext_skills = rc.get("allow_external_skills") or []
            if not rc.get("allow_external", False) and skill_name not in allow_ext_skills:
                d = Decision(False, f"角色 {actor} 无外呼权限（side_effect=external）"
                                    f"，且 {skill_name} 不在 allow_external_skills 白名单")
                self.tracer.audit("policy_check", actor, skill_name, "deny",
                                  {**detail, "reason": d.reason})
                return d

        if side_effect == "write" and not rc.get("allow_write", False):
            d = Decision(False, f"角色 {actor} 无写权限（side_effect=write）")
            self.tracer.audit("policy_check", actor, skill_name, "deny",
                              {**detail, "reason": d.reason})
            return d

        # 只写本次运行目录的动作（报告渲染等）不需要审批——它可丢弃、不污染知识库。
        # 需要审批的是"写共享状态"和"高风险"两类。
        require_for_shared = self.cfg.get("approval", {}).get("require_for_shared_write", True)
        need_approval = (risk == "high"
                         or (side_effect == "write" and scope == "shared_state"
                             and require_for_shared))
        if not need_approval:
            self.tracer.audit("policy_check", actor, skill_name, "allow", detail)
            return Decision(True, "低风险只读动作，自动放行")

        approved = self.approve_all or bool(self.approvals.get(skill_name))
        if approved:
            self.tracer.audit("approval", actor, skill_name, "allow",
                              {**detail, "mode": "cli --approve" if self.approve_all else "approvals.json"})
            return Decision(True, "高风险动作已获人工批准", requires_approval=True, approved=True)

        self.tracer.audit("approval", actor, skill_name, "pending",
                          {**detail, "hint": "重跑并加 --approve，或在 approvals.json 中授权"})
        return Decision(False, "高风险动作待人工批准（Human-in-the-Loop）",
                        requires_approval=True, approved=False)

    # -- 回滚 --------------------------------------------------------------
    def snapshot(self, paths, actor: str, action: str) -> str:
        """写操作前对受影响文件做快照，返回 rollback_ref。"""
        os.makedirs(self.rollback_dir, exist_ok=True)
        ref = f"rb-{int(time.time() * 1000)}"
        target = os.path.join(self.rollback_dir, ref)
        os.makedirs(target, exist_ok=True)
        manifest = []
        for p in paths:
            if os.path.exists(p):
                dst = os.path.join(target, os.path.basename(p))
                shutil.copy2(p, dst)
                manifest.append({"origin": os.path.abspath(p), "backup": os.path.abspath(dst)})
            else:
                manifest.append({"origin": os.path.abspath(p), "backup": None, "note": "写入前不存在"})
        with open(os.path.join(target, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        self.tracer.audit("write", actor, action, "snapshot",
                          {"files": len(manifest)}, rollback_ref=ref)
        return ref

    @staticmethod
    def restore(run_dir: str) -> Dict[str, Any]:
        """把某次运行的所有写操作还原（按时间倒序）。"""
        rb = os.path.join(run_dir, "rollback")
        if not os.path.isdir(rb):
            return {"restored": 0, "detail": "该运行没有写操作快照"}
        n = 0
        detail = []
        for ref in sorted(os.listdir(rb), reverse=True):
            mpath = os.path.join(rb, ref, "manifest.json")
            if not os.path.exists(mpath):
                continue
            with open(mpath, encoding="utf-8") as f:
                manifest = json.load(f)
            for item in manifest:
                if item.get("backup") and os.path.exists(item["backup"]):
                    shutil.copy2(item["backup"], item["origin"])
                    n += 1
                    detail.append(item["origin"])
                elif item.get("backup") is None and os.path.exists(item["origin"]):
                    os.remove(item["origin"])
                    n += 1
                    detail.append(item["origin"] + " (removed)")
        return {"restored": n, "detail": detail}
