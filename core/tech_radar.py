"""
技术雷达系统
预测技术趋势、风险评估、迁移建议
"""

import json
import time
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, asdict
from pathlib import Path
from enum import Enum


class TechCategory(Enum):
    """技术类别"""
    LANGUAGES = "languages"
    FRAMEWORKS = "frameworks"
    TOOLS = "tools"
    ARCHITECTURES = "architectures"
    METHODOLOGIES = "methodologies"


class TechMaturity(Enum):
    """技术成熟度"""
    EMERGING = "emerging"      # 新兴
    GROWING = "growing"        # 成长
    MATURE = "mature"          # 成熟
    DECLINING = "declining"    # 衰退


@dataclass
class TechItem:
    """技术条目"""
    name: str
    category: TechCategory
    maturity: TechMaturity
    risk_score: float  # 0-1, 1表示高风险
    adoption_rate: float  # 0-1, 采用率
    trend: str  # up, down, stable
    last_updated: float
    metadata: Dict[str, Any]
    
    def __post_init__(self):
        # 确保枚举类型正确
        if isinstance(self.category, str):
            self.category = TechCategory(self.category)
        if isinstance(self.maturity, str):
            self.maturity = TechMaturity(self.maturity)
    
    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["category"] = self.category.value
        data["maturity"] = self.maturity.value
        return data


class TechRadar:
    """技术雷达管理器"""
    
    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(exist_ok=True)
        
        self.radar_file = self.data_dir / "tech_radar.json"
        self.trends_file = self.data_dir / "trends.json"
        
        self.items: List[TechItem] = []
        self.trends: Dict[str, Any] = {}
        
        self._load_data()
    
    def _load_data(self):
        """加载数据"""
        if self.radar_file.exists():
            try:
                with open(self.radar_file, 'r', encoding='utf-8') as f:
                    content = f.read().strip()
                    if content:  # 检查文件是否为空
                        data = json.loads(content)
                        items_data = data.get("items", [])
                        self.items = []
                        for item_dict in items_data:
                            # 确保枚举类型正确
                            if "category" in item_dict and isinstance(item_dict["category"], str):
                                item_dict["category"] = TechCategory(item_dict["category"])
                            if "maturity" in item_dict and isinstance(item_dict["maturity"], str):
                                item_dict["maturity"] = TechMaturity(item_dict["maturity"])
                            self.items.append(TechItem(**item_dict))
            except Exception as e:
                print(f"加载技术雷达数据失败: {e}")
                self.items = []
        
        if self.trends_file.exists():
            try:
                with open(self.trends_file, 'r', encoding='utf-8') as f:
                    content = f.read().strip()
                    if content:
                        self.trends = json.loads(content)
            except Exception as e:
                print(f"加载趋势数据失败: {e}")
                self.trends = {}
    
    def _save_data(self):
        """保存数据"""
        with open(self.radar_file, 'w', encoding='utf-8') as f:
            json.dump({
                "items": [item.to_dict() for item in self.items],
                "last_updated": time.time()
            }, f, indent=2, ensure_ascii=False)
        
        with open(self.trends_file, 'w', encoding='utf-8') as f:
            json.dump(self.trends, f, indent=2, ensure_ascii=False)
    
    def add_tech_item(self, name: str, category: TechCategory, 
                      maturity: TechMaturity, risk_score: float = 0.5,
                      adoption_rate: float = 0.5, trend: str = "stable",
                      metadata: Optional[Dict[str, Any]] = None) -> str:
        """添加技术条目"""
        item = TechItem(
            name=name,
            category=category,
            maturity=maturity,
            risk_score=risk_score,
            adoption_rate=adoption_rate,
            trend=trend,
            last_updated=time.time(),
            metadata=metadata or {}
        )
        
        self.items.append(item)
        self._save_data()
        
        return name
    
    def update_tech_item(self, name: str, updates: Dict[str, Any]) -> bool:
        """更新技术条目"""
        for item in self.items:
            if item.name == name:
                for key, value in updates.items():
                    if hasattr(item, key):
                        if key == "category":
                            value = TechCategory(value)
                        elif key == "maturity":
                            value = TechMaturity(value)
                        setattr(item, key, value)
                
                item.last_updated = time.time()
                self._save_data()
                return True
        
        return False
    
    def get_tech_item(self, name: str) -> Optional[TechItem]:
        """获取技术条目"""
        for item in self.items:
            if item.name == name:
                return item
        return None
    
    def get_items_by_category(self, category: TechCategory) -> List[TechItem]:
        """按类别获取技术条目"""
        return [item for item in self.items if item.category == category]
    
    def get_items_by_maturity(self, maturity: TechMaturity) -> List[TechItem]:
        """按成熟度获取技术条目"""
        return [item for item in self.items if item.maturity == maturity]
    
    def get_high_risk_items(self, threshold: float = 0.7) -> List[TechItem]:
        """获取高风险技术"""
        return [item for item in self.items if item.risk_score > threshold]
    
    def get_trending_items(self, trend: str = "up") -> List[TechItem]:
        """获取趋势上升的技术"""
        return [item for item in self.items if item.trend == trend]
    
    async def analyze_trends(self, current_tech_stack: List[str]) -> Dict[str, Any]:
        """分析技术趋势"""
        # 分析当前技术栈
        stack_analysis = self._analyze_stack(current_tech_stack)
        
        # 预测未来趋势
        future_trends = self._predict_trends()
        
        # 风险评估
        risk_assessment = self._assess_risks(current_tech_stack)
        
        # 生成建议
        recommendations = self._generate_recommendations(stack_analysis, risk_assessment)
        
        return {
            "current_stack": stack_analysis,
            "future_trends": future_trends,
            "risk_assessment": risk_assessment,
            "recommendations": recommendations,
            "generated_at": time.time()
        }
    
    def _analyze_stack(self, tech_stack: List[str]) -> Dict[str, Any]:
        """分析技术栈"""
        analysis = {
            "total_tech": len(tech_stack),
            "by_category": {},
            "by_maturity": {},
            "avg_risk": 0
        }
        
        risk_sum = 0
        for tech in tech_stack:
            item = self.get_tech_item(tech)
            if item:
                # 按类别统计
                category = item.category.value
                analysis["by_category"][category] = analysis["by_category"].get(category, 0) + 1
                
                # 按成熟度统计
                maturity = item.maturity.value
                analysis["by_maturity"][maturity] = analysis["by_maturity"].get(maturity, 0) + 1
                
                risk_sum += item.risk_score
        
        analysis["avg_risk"] = risk_sum / len(tech_stack) if tech_stack else 0
        
        return analysis
    
    def _predict_trends(self) -> List[Dict[str, Any]]:
        """预测未来趋势"""
        trends = []
        
        for item in self.items:
            if item.trend == "up" and item.maturity in [TechMaturity.EMERGING, TechMaturity.GROWING]:
                trends.append({
                    "tech": item.name,
                    "category": item.category.value,
                    "confidence": 0.8,
                    "timeframe": "6-12 months"
                })
        
        return trends
    
    def _assess_risks(self, tech_stack: List[str]) -> Dict[str, Any]:
        """风险评估"""
        risks = []
        total_risk = 0
        
        for tech in tech_stack:
            item = self.get_tech_item(tech)
            if item and item.risk_score > 0.6:
                risks.append({
                    "tech": tech,
                    "risk_score": item.risk_score,
                    "maturity": item.maturity.value,
                    "reason": f"High risk score ({item.risk_score}) with {item.maturity.value} maturity"
                })
                total_risk += item.risk_score
        
        return {
            "total_risks": len(risks),
            "risks": risks,
            "avg_risk": total_risk / len(tech_stack) if tech_stack else 0
        }
    
    def _generate_recommendations(self, stack_analysis: Dict[str, Any], 
                                  risk_assessment: Dict[str, Any]) -> List[Dict[str, Any]]:
        """生成建议"""
        recommendations = []
        
        # 基于风险的建议
        if risk_assessment["avg_risk"] > 0.7:
            recommendations.append({
                "type": "risk_mitigation",
                "priority": "high",
                "action": "考虑替换高风险技术",
                "details": f"平均风险评分 {risk_assessment['avg_risk']:.2f} 超过阈值"
            })
        
        # 基于成熟度的建议
        if "emerging" in stack_analysis["by_maturity"]:
            recommendations.append({
                "type": "maturity",
                "priority": "medium",
                "action": "监控新兴技术的稳定性",
                "details": "技术栈中包含新兴技术，建议持续监控"
            })
        
        # 基于类别的建议
        if stack_analysis["by_category"].get("frameworks", 0) > 3:
            recommendations.append({
                "type": "simplification",
                "priority": "low",
                "action": "考虑简化框架依赖",
                "details": "框架数量较多，可能增加维护成本"
            })
        
        return recommendations
    
    def get_statistics(self) -> Dict[str, Any]:
        """获取统计信息"""
        total = len(self.items)
        
        category_counts = {}
        maturity_counts = {}
        risk_sum = 0
        
        for item in self.items:
            category_counts[item.category.value] = category_counts.get(item.category.value, 0) + 1
            maturity_counts[item.maturity.value] = maturity_counts.get(item.maturity.value, 0) + 1
            risk_sum += item.risk_score
        
        return {
            "total_items": total,
            "by_category": category_counts,
            "by_maturity": maturity_counts,
            "avg_risk": risk_sum / total if total > 0 else 0
        }


# 全局实例
_tech_radar_instance = None

def get_tech_radar(data_dir: str = "data") -> TechRadar:
    """获取技术雷达单例"""
    global _tech_radar_instance
    if _tech_radar_instance is None:
        _tech_radar_instance = TechRadar(data_dir)
    return _tech_radar_instance
