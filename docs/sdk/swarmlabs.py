"""
SwarmLabs Python SDK（轻量，零依赖）
用法:
    from swarmlabs import SwarmLabs
    client = SwarmLabs(base_url="https://swarmlabs.tools", token="stk_xxx")
    # 单次预测
    r = client.run("suzuki", {"temperature_C": 80, "catalyst_loading": 0.02, "time_h": 4})
    print(r["result"], r["confidence_grade"], r["uncertainty"])
    # 文献溯源
    print(r["literature_support"])
    # 参数扫描（趋势图）
    sweep = client.sweep("suzuki", sweep_param="temperature_C", points=12)
    for p in sweep["series"]:
        print(p["value"], p["result"], p["confidence_grade"])
    # 负样本库
    negs = client.negative_samples("suzuki")
    # 账户余额
    print(client.account())
"""
import urllib.request
import json


class SwarmLabs:
    def __init__(self, base_url="https://swarmlabs.tools", token=None):
        self.base = base_url.rstrip("/")
        self.token = token

    def _headers(self, json_body=True):
        h = {"Accept": "application/json", "User-Agent": "SwarmLabs-Python-SDK/3.4.0"}
        if json_body:
            h["Content-Type"] = "application/json"
        if self.token:
            h["Authorization"] = "Bearer " + self.token
        return h

    def _req(self, method, path, body=None):
        url = self.base + "/" + path.lstrip("/")
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, headers=self._headers(body is not None), method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                detail = json.loads(e.read().decode("utf-8"))
            except Exception:
                detail = {"error": e.reason}
            return {"_error": e.code, **detail}

    # ---- 端点封装 ----
    def run(self, engine, params=None):
        return self._req("POST", f"api/v2/run/{engine}", params or {})

    def sweep(self, engine, sweep_param="temperature_C", points=10, fixed=None):
        return self._req("POST", "api/v2/sweep", {
            "engine": engine, "sweep_param": sweep_param,
            "points": points, "fixed": fixed or {}
        })

    def literature(self, engine=None):
        return self._req("GET", "api/v2/literature" + (f"/{engine}" if engine else ""))

    def negative_samples(self, engine=None):
        return self._req("GET", "api/v2/negative-samples" + (f"/{engine}" if engine else ""))

    def enterprise(self):
        return self._req("GET", "api/v2/enterprise")

    def account(self):
        return self._req("GET", "api/v2/account")

    def feedback(self, engine, predicted, actual, signal=None, context=None):
        """提交真实实验反馈，驱动数据飞轮与主动学习闭环。"""
        body = {"engine": engine, "predicted": predicted, "actual": actual}
        if signal:
            body["signal"] = signal
        if context:
            body["context"] = context
        return self._req("POST", "api/v2/feedback", body)

    def feedback_summary(self):
        """飞轮可见化：真实用户反馈统计与每引擎观测 RMSE。"""
        return self._req("GET", "api/v2/feedback/summary")

    def experiment_plan(self, engine, fixed=None):
        """干湿闭环种子：由预测推导的标准化实验方案模板。"""
        return self._req("POST", f"api/v2/experiment-plan/{engine}", fixed or {})

    def graph(self, engine=None):
        """知识图谱：全局统计(engine=None)或某引擎邻域。"""
        return self._req("GET", f"api/v2/graph/{engine}" if engine else "api/v2/graph")

    def physics_informed(self, engine, **params):
        """物理信息参数化代理(诚实版PINN)：可微分动力学形式预测。"""
        return self._req("POST", f"api/v2/pi/{engine}", params)

    def suggest(self, engine):
        """主动学习/贝叶斯优化：返回 EI 推荐的下一步实验参数。"""
        return self._req("GET", f"api/v2/suggest/{engine}")

    def model_state(self, engine):
        """当前高斯过程代理状态(训练点数/是否含反馈/参数边界)。"""
        return self._req("GET", f"api/v2/model/{engine}")

    def multifidelity(self, engine=None, smiles=None, deltaG=None, method=None):
        """多保真度(xtb)集成：engine=None 时提交先验, 否则查询某引擎已存先验。"""
        if engine is None:
            raise ValueError("engine required")
        if smiles is None and deltaG is None:
            return self._req("GET", f"api/v2/multifidelity/{engine}")
        return self._req("POST", "api/v2/multifidelity",
                         {"engine": engine, "smiles": smiles, "deltaG": deltaG, "method": method})

    # ---- 用户闭环：数据飞轮 / 积分 / 自带API（差异化能力） ----
    def my_experiments(self, engine=None):
        """取回当前用户提交的全部/指定引擎实验结果（支撑再应用）。"""
        path = "api/v2/my-experiments" + (f"/{engine}" if engine else "")
        return self._req("GET", path)

    def reapply(self, target_engine, source_id=None, source_engine=None, use_my_experiments=False):
        """实验结果再应用：把已验证实验注入目标引擎 GP 作为训练点，生成下一轮建议。"""
        body = {"target_engine": target_engine}
        if source_id:
            body["source_id"] = source_id
        elif use_my_experiments:
            body["use_my_experiments"] = True
            if source_engine:
                body["source_engine"] = source_engine
        return self._req("POST", "api/v2/reapply", body)

    def leaderboard(self):
        """贡献积分榜（邮箱脱敏，按累计赚取积分排名）。"""
        return self._req("GET", "api/v2/leaderboard")

    def get_apikey(self):
        """读取自带 API 配置（掩码返回，不含明文）。"""
        return self._req("GET", "api/v2/account/apikey")

    def save_apikey(self, provider, api_key, model=None, base_url=None):
        """保存自带 LLM 密钥（平台零成本、数据不出你的账号）。"""
        body = {"provider": provider, "apiKey": api_key}
        if model:
            body["model"] = model
        if base_url:
            body["baseUrl"] = base_url
        return self._req("POST", "api/v2/account/apikey", body)

    def delete_apikey(self):
        """移除自带 LLM 密钥（POST {action:'delete'}，绕过 CDN 对 DELETE 的 405 拦截）。"""
        return self._req("POST", "api/v2/account/apikey", {"action": "delete"})

    def llm(self, messages, model=None):
        """自带 API LLM 代理：经你的密钥路由，平台零成本、数据不出账号。"""
        body = {"messages": messages}
        if model:
            body["model"] = model
        return self._req("POST", "api/v2/llm", body)


if __name__ == "__main__":
    c = SwarmLabs()
    print("health:", c._req("GET", "api/v2/health").get("features", [])[:3])
    print("suzuki:", c.run("suzuki", {"temperature_C": 80, "catalyst_loading": 0.02, "time_h": 4}))
