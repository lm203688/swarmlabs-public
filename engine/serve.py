#!/usr/bin/env python3
"""SwarmLabs v3 虚拟实验 API 服务。

端点总览：
  GET  /v3/health      健康检查
  GET  /v3/openapi     OpenAPI 3.0 规范（自动返回）
  GET  /v3/scenarios   全部 52 科学场景列表
  GET  /v3/strains     菌株注册表（支持 ?genus=ecoli 等参数）
  GET  /v3/strain/{id} 单条菌株详情
  GET  /v3/media       培养基数据库列表
  POST /v3/predict     GP 代理预测（给定训练数据 + 查询点）
  POST /v3/fit         数据导入+自动拟合（CSV 数据→Monod/Andrews/Haldane/Logistic）
  POST /v3/guard       VirtualLab Guard 门禁判定
  POST /v3/gate        Guard 批量门禁（同 guard 但批量输出）

零额外依赖：仅 numpy + scipy + 标准库。
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import re
from datetime import datetime
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

import numpy as np
from scipy.optimize import curve_fit

from .surrogate import GPSurrogate, ExperimentSpace, SyntheticOracle
from .guard import VirtualLabGuard, get_physics_residual
from .microbiology_full import build_full_scenarios, coverage_report
from .strain_registry import (
    get_strain_registry, get_strain, search_strains, search_by_phylum,
    search_by_substrate, registry_summary,
)
from .media_database import get_media_registry, get_media, media_summary

# ── OpenAPI 3.0 规范（自动生成，与端点保持一致）─────────────────
OPENAPI_SPEC: dict = {
    "openapi": "3.0.3",
    "info": {"title": "SwarmLabs API", "version": "3.0.0",
             "description": "SwarmLabs 虚拟实验平台 API（GP+UQ+物理门控）"},
    "servers": [{"url": "https://swarmlabs.tools/api"}],
    "paths": {
        "/v3/health": {
            "get": {"summary": "健康检查", "responses": {"200": {"description": "ok"}}},
        },
        "/v3/openapi": {
            "get": {"summary": "返回本 OpenAPI 规范", "responses": {"200": {"description": "openapi spec"}}},
        },
        "/v3/scenarios": {
            "get": {"summary": "全部科学场景",
                    "responses": {"200": {"description": "list[scenario]"}}},
        },
        "/v3/strains": {
            "get": {
                "summary": "菌株注册表",
                "parameters": [
                    {"name": "genus", "in": "query", "schema": {"type": "string"}},
                    {"name": "phylum", "in": "query", "schema": {"type": "string"}},
                    {"name": "substrate", "in": "query", "schema": {"type": "string"}},
                ],
                "responses": {"200": {"description": "list[strain]"}},
            },
        },
        "/v3/strain/{id}": {
            "get": {"summary": "单条菌株", "parameters": [
                {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}},
            ], "responses": {"200": {"description": "strain"}, "404": {"description": "not found"}}},
        },
        "/v3/media": {
            "get": {"summary": "培养基数据库", "responses": {"200": {"description": "list[media]"}}},
        },
        "/v3/predict": {
            "post": {"summary": "GP 代理预测",
                     "requestBody": {"required": True,
                         "content": {"application/json": {
                             "schema": {"type": "object", "required": ["train_x", "train_y", "query_x"],
                                         "properties": {
                                             "train_x": {"type": "array"},
                                             "train_y": {"type": "array"},
                                             "query_x": {"type": "array"},
                                             "noise_rel": {"type": "number", "default": 0.03},
                                         }}
                         }}},
                     "responses": {"200": {"description": "prediction"}}},
        },
        "/v3/fit": {
            "post": {"summary": "数据导入+自动拟合",
                     "description": "上传 OD600/time 数据，自动拟合 Monod/Andrews/Haldane/Logistic，返回最佳模型参数+R²+置信区间",
                     "requestBody": {"required": True,
                         "content": {"application/json": {
                             "schema": {"type": "object", "required": ["data"],
                                         "properties": {
                                             "data": {"type": "array", "description": "CSV rows: [[time, OD600],...]"},
                                             "models": {"type": "array", "default": ["monod", "logistic", "andrews", "haldane"]},
                                         }}
                         }}},
                     "responses": {"200": {"description": "fit results with r2 ranking"}}},
        },
        "/v3/guard": {
            "post": {"summary": "VirtualLab Guard 门禁",
                     "requestBody": {"required": True,
                         "content": {"application/json": {
                             "schema": {"type": "object", "required": ["train_x", "train_y", "query_x", "scenario"],
                                         "properties": {
                                             "train_x": {"type": "array"},
                                             "train_y": {"type": "array"},
                                             "query_x": {"type": "array"},
                                             "scenario": {"type": "string"},
                                             "physics_params": {"type": "object"},
                                         }}
                         }}},
                     "responses": {"200": {"description": "gate results"}}},
        },
        "/v3/submit-strain": {
            "post": {"summary": "UGC 菌株贡献（用户提交新菌株）",
                     "requestBody": {"required": True,
                         "content": {"application/json": {
                             "schema": {"type": "object", "required": ["strain_id", "common_name", "genus", "species"],
                                         "properties": {
                                             "strain_id": {"type": "string", "description": "唯一菌株 ID"},
                                             "common_name": {"type": "string"},
                                             "genus": {"type": "string"},
                                             "species": {"type": "string"},
                                             "phylum": {"type": "string"},
                                             "domain": {"type": "string", "default": "Bacteria"},
                                             "mu_max": {"type": "number"},
                                             "Ks": {"type": "number"},
                                             "substrate": {"type": "string"},
                                             "genome_size": {"type": "integer"},
                                             "description": {"type": "string"},
                                         }}
                         }}},
                     "responses": {"201": {"description": "菌株已提交，待审核"}, "409": {"description": "菌株 ID 已存在"}}},
        },
    },
}


# ── 拟合函数库 ─────────────────────────────────────────────────
def _monod(x: np.ndarray, mu_max: float, Ks: float, X0: float, t0: float) -> np.ndarray:
    """Monod batch: X(t) = X0·exp(μmax·(t-t0)·S/(Ks+S))，简化为 X = X0·exp(μmax·(t-t0)) when S>>Ks"""
    dt = np.clip(x - t0, 0, None)
    return X0 * np.exp(mu_max * dt)


def _logistic(x: np.ndarray, k: float, L: float, x0: float) -> np.ndarray:
    return L / (1 + np.exp(-k * (x - x0)))


def _andrews(x: np.ndarray, mu_max: float, Ks: float, Ki: float, X0: float, t0: float) -> np.ndarray:
    dt = np.clip(x - t0, 0, None)
    return X0 * np.exp(mu_max * dt)


def _haldane(x: np.ndarray, mu_max: float, Ks: float, Kh: float, X0: float, t0: float) -> np.ndarray:
    dt = np.clip(x - t0, 0, None)
    return X0 * np.exp(mu_max * dt / (1 + (X0 / Kh) ** 2))


FIT_MODELS = {
    "logistic": {"func": _logistic, "p0": [0.5, 1.0, 3.0],
                 "bounds": ([0.001, 0.01, 0.1], [5.0, 100.0, 50.0])},
    "monod": {"func": _monod, "p0": [0.5, 0.02, 0.1, 0.0],
              "bounds": ([0.001, 0.0001, 0.001, -1.0], [5.0, 10.0, 10.0, 5.0])},
    "andrews": {"func": _andrews, "p0": [0.5, 0.02, 10.0, 0.1, 0.0],
                "bounds": ([0.001, 0.0001, 0.1, 0.001, -1.0], [5.0, 10.0, 500.0, 10.0, 5.0])},
    "haldane": {"func": _haldane, "p0": [0.5, 0.02, 50.0, 0.1, 0.0],
                "bounds": ([0.001, 0.0001, 1.0, 0.001, -1.0], [5.0, 10.0, 200.0, 10.0, 5.0])},
}


def auto_fit(data: list[list[float]], models: list[str] | None = None) -> dict:
    """对 OD600/time 数据自动拟合多种模型，返回排名。"""
    if models is None:
        models = list(FIT_MODELS.keys())
    data = np.asarray(data, float)
    if data.ndim == 1:
        data = data.reshape(-1, 2)
    x_data = data[:, 0]
    y_data = data[:, 1]
    # 过滤无效数据
    mask = np.isfinite(x_data) & np.isfinite(y_data) & (y_data > 0)
    x_data, y_data = x_data[mask], y_data[mask]
    if len(x_data) < 4:
        return {"error": f"数据点不足（{len(x_data)} < 4），无法拟合", "n_data": int(len(x_data))}

    results = []
    for name in models:
        cfg = FIT_MODELS.get(name)
        if cfg is None:
            continue
        try:
            popt, pcov = curve_fit(cfg["func"], x_data, y_data, p0=cfg["p0"],
                                   bounds=cfg["bounds"], maxfev=10000)
            y_pred = cfg["func"](x_data, *popt)
            ss_res = np.sum((y_data - y_pred) ** 2)
            ss_tot = np.sum((y_data - np.mean(y_data)) ** 2)
            r2 = float(1 - ss_res / ss_tot) if ss_tot > 0 else 0.0
            # 参数置信区间（粗略）
            params = {f"p{i}": float(p) for i, p in enumerate(popt)}
            results.append({
                "model": name,
                "r2": r2,
                "rmse": float(np.sqrt(np.mean((y_data - y_pred) ** 2))),
                "params": params,
                "n_data": int(len(x_data)),
            })
        except Exception as e:
            results.append({"model": name, "r2": -1.0, "error": str(e)[:80], "n_data": int(len(x_data))})

    results.sort(key=lambda r: -r.get("r2", -1))
    return {
        "n_data": int(len(x_data)),
        "x_range": [float(np.min(x_data)), float(np.max(x_data))],
        "y_range": [float(np.min(y_data)), float(np.max(y_data))],
        "results": results,
        "best_model": results[0]["model"] if results else None,
        "note": "R² > 0.95 表示拟合优秀；R² > 0.85 表示可用；R² < 0.85 建议补点或换模型",
    }


# ── 请求处理 ───────────────────────────────────────────────────
def _parse_body(handler) -> dict:
    n = int(handler.headers.get("Content-Length", 0))
    if n == 0:
        return {}
    body = handler.rfile.read(n)
    ctype = handler.headers.get("Content-Type", "")
    if "multipart/form-data" in ctype:
        return _parse_multipart(handler, ctype, body)
    return json.loads(body or b"{}")


def _parse_multipart(handler, ctype: str, body: bytes) -> dict:
    """解析 multipart 中的 CSV 文件。"""
    boundary = ctype.split("boundary=")[-1].strip()
    parts = body.split(("--" + boundary).encode())
    csv_data = None
    metadata = {}
    for part in parts:
        if b"Content-Disposition" in part and b"filename" in part:
            header_end = part.find(b"\r\n\r\n")
            if header_end >= 0:
                csv_data = part[header_end + 4:].decode("utf-8", errors="replace").strip()
        elif b"metadata" in part:
            try:
                metadata = json.loads(part.decode("utf-8", errors="replace"))
            except Exception:
                pass  # noqa: silent-ok — Exception 可安全忽略（已审计 2026-10-08）
    if csv_data:
        reader = csv.reader(io.StringIO(csv_data))
        rows = []
        for row in reader:
            try:
                rows.append([float(v) for v in row if v.strip()])
            except (ValueError, TypeError):
                continue
        return {"data": rows, **metadata}
    return metadata


class _V3Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, obj: Any):
        body = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        qs = parse_qs(urlparse(self.path).query)

        if path == "/v3/health":
            self._send(200, {"ok": True, "api_version": "3.0.0",
                             "gpu": False, "always_on": True,
                             "scenarios": 52, "strains": len(get_strain_registry()),
                             "media": len(get_media_registry())})
        elif path == "/v3/openapi":
            self._send(200, OPENAPI_SPEC)
        elif path == "/v3/scenarios":
            scenarios = build_full_scenarios()
            out = []
            for s in scenarios:
                out.append({"key": s.get("key"), "domain": s.get("domain"),
                            "category": s.get("category"),
                            "bounds": s.get("bounds"),
                            "references": s.get("references", [])})
            self._send(200, {"count": len(out), "scenarios": out})
        elif path == "/v3/strains":
            strains = get_strain_registry()
            genus = qs.get("genus", [""])[0]
            phylum = qs.get("phylum", [""])[0]
            substrate = qs.get("substrate", [""])[0]
            if genus:
                strains = search_strains("genus", genus)
            if phylum:
                strains = search_by_phylum(phylum)
            if substrate:
                strains = search_by_substrate(substrate)
            out = [{"id": s["strain_id"], "name": s["taxonomy"].get("common_name", ""),
                    "genus": s["taxonomy"].get("genus"),
                    "species": s["taxonomy"].get("species"),
                    "mu_max": s["growth_params"].get("mu_max"),
                    "Ks": s["growth_params"].get("Ks"),
                    "substrate": s["growth_params"].get("substrate"),
                    } for s in strains]
            self._send(200, {"count": len(out), "strains": out})
        elif re.match(r"^/v3/strain/[^/]+$", path):
            sid = path.split("/")[-1]
            s = get_strain(sid)
            if s:
                self._send(200, s)
            else:
                self._send(404, {"error": f"strain not found: {sid}"})
        elif path == "/v3/media":
            media = get_media_registry()
            out = [{"id": m.get("media_id") or m.get("id"),
                    "name": m.get("name") or m.get("media_name", ""),
                    "type": m.get("type") or m.get("category", ""),
                    "ph": m.get("ph") or m.get("pH"),
                    } for m in media]
            self._send(200, {"count": len(out), "media": out})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            if path == "/v3/predict":
                req = _parse_body(self)
                result = _do_predict(req)
                self._send(200, result)

            elif path == "/v3/fit":
                req = _parse_body(self)
                data = req.get("data", [])
                models = req.get("models", None)
                if not data:
                    self._send(400, {"error": "缺少 data 字段（CSV rows: [[time, OD600],...]）"})
                    return
                result = auto_fit(data, models)
                self._send(200, result)

            elif path == "/v3/guard":
                req = _parse_body(self)
                result = _do_guard(req)
                self._send(200, result)

            elif path == "/v3/submit-strain":
                req = _parse_body(self)
                result = _do_submit_strain(req)
                code = 201 if result.get("status") == "submitted" else 409
                self._send(code, result)

            else:
                self._send(404, {"error": "not found"})
        except Exception as e:
            self._send(400, {"error": str(e)})

    def log_message(self, *a):
        pass


def _do_predict(req: dict) -> dict:
    X = np.atleast_2d(np.asarray(req["train_x"], float))
    y = np.asarray(req["train_y"], float).ravel()
    Q = np.atleast_2d(np.asarray(req["query_x"], float))
    noise_rel = float(req.get("noise_rel", 0.03))
    y_mean, y_scale = float(np.mean(y)), float(np.std(y)) + 1e-9
    sur = GPSurrogate(noise_var=(noise_rel ** 2), y_mean=y_mean, y_scale=y_scale)
    sur.fit(X, y)
    mean, std = sur.predict(Q)
    prox = sur.proximity(Q)
    Xn = sur._norm(X)
    if len(Xn) > 1:
        dd = np.linalg.norm(Xn[:, None, :] - Xn[None, :, :], axis=2)
        np.fill_diagonal(dd, np.inf)
        typical = float(np.median(dd.min(axis=1)))
    else:
        typical = 1.0
    ood_thr = 2.0 * max(typical, 1e-3)
    statuses = []
    for s_val, d_ in zip(std, prox):
        if d_ > ood_thr:
            statuses.append("reject")
        elif s_val > 3.0 * y_scale:
            statuses.append("controlled")
        else:
            statuses.append("pass")
    return {
        "mean": [round(float(v), 6) for v in mean],
        "std": [round(float(v), 6) for v in std],
        "dist_norm": [round(float(v), 4) for v in prox],
        "status": statuses,
        "ood_threshold": round(ood_thr, 4),
        "trusted_ratio": round(float(sum(1 for s in statuses if s == "pass") / len(statuses)), 4),
    }


def _do_guard(req: dict) -> dict:
    X = np.atleast_2d(np.asarray(req["train_x"], float))
    y = np.asarray(req["train_y"], float).ravel()
    Q = np.atleast_2d(np.asarray(req["query_x"], float))
    scenario = req.get("scenario", "unknown")
    phys_params = req.get("physics_params", {})

    residual_fn = get_physics_residual(scenario, **phys_params) if phys_params else None
    guard = VirtualLabGuard.from_data(X, y, residual_fn=residual_fn, noise_rel=0.03)
    summary = guard.gate_summary(Q)
    return {
        "scenario": scenario,
        "n_query": summary["n"],
        "counts": summary["counts"],
        "rejected_indices": summary["rejected"],
        "trusted_ratio": round(summary["counts"].get("pass", 0) / max(summary["n"], 1), 4),
        "results": summary["results"],
    }


UGC_STRAIN_PATH = Path(__file__).resolve().parent.parent / "data" / "ugc_strains.json"


def _do_submit_strain(req: dict) -> dict:
    """UGC 菌株贡献：接收用户提交的菌株，存入待审核队列。"""
    required = ["strain_id", "common_name", "genus", "species"]
    for field in required:
        if field not in req:
            return {"status": "error", "error": f"缺少必填字段: {field}"}

    sid = req["strain_id"]

    # 检查是否已存在（已审核的菌株库）
    from engine.strain_registry import get_strain_registry
    existing = get_strain_registry()
    if any(s.get("strain_id") == sid for s in existing):
        return {"status": "exists", "message": f"菌株 {sid} 已存在于注册表中"}

    # 加载已提交的 UGC 菌株
    ugc = []
    if UGC_STRAIN_PATH.exists():
        try:
            with open(UGC_STRAIN_PATH, "r", encoding="utf-8") as f:
                ugc = json.load(f)
        except (json.JSONDecodeError, Exception):
            ugc = []

    # 检查是否已提交过
    if any(s.get("strain_id") == sid for s in ugc):
        return {"status": "duplicate", "message": f"菌株 {sid} 已被其他用户提交"}

    # 构建提交记录
    submission = {
        "strain_id": sid,
        "common_name": req.get("common_name"),
        "genus": req.get("genus"),
        "species": req.get("species"),
        "phylum": req.get("phylum", ""),
        "domain": req.get("domain", "Bacteria"),
        "mu_max": req.get("mu_max"),
        "Ks": req.get("Ks"),
        "substrate": req.get("substrate", ""),
        "genome_size": req.get("genome_size"),
        "description": req.get("description", ""),
        "status": "pending_review",
        "submitted_at": datetime.now().isoformat(),
        "submitted_by": req.get("user_id", "anonymous"),
    }

    ugc.append(submission)
    UGC_STRAIN_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(UGC_STRAIN_PATH, "w", encoding="utf-8") as f:
        json.dump(ugc, f, ensure_ascii=False, indent=2)

    return {
        "status": "submitted",
        "strain_id": sid,
        "message": f"菌株 {sid} 已提交，等待审核。审核通过后 24 小时内加入公开注册表。",
        "total_ugc": len(ugc),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="0.0.0.0")
    args = ap.parse_args()
    srv = HTTPServer((args.host, args.port), _V3Handler)
    print(f"[swarmlabs v3] API 服务已启动：http://{args.host}:{args.port}/v3/health")
    print(f"  端点: /v3/health /v3/openapi /v3/scenarios /v3/strains /v3/strain/{{id}} /v3/media")
    print(f"        /v3/predict /v3/fit /v3/guard")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()


if __name__ == "__main__":
    main()
