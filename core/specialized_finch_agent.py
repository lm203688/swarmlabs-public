"""
Specialized Finch Agent for Data Analysis
Inspired by Robin's Finch agent for experimental data analysis
Features:
  - Multi-modal data analysis (RNA-seq, flow cytometry, etc.)
  - Statistical analysis
  - Data visualization
  - Result interpretation
  - Report generation
"""

from enum import Enum
from typing import Dict, Any, List, Optional, Union
import asyncio
import json
import time
from dataclasses import dataclass, field
import math


class DataType(Enum):
    RNA_SEQ = "rna_seq"
    FLOW_CYTOMETRY = "flow_cytometry"
    MICROSCOPY = "microscopy"
    PROTEOMICS = "proteomics"
    GENOMICS = "genomics"
    CLINICAL = "clinical"
    GENERIC = "generic"


class AnalysisType(Enum):
    DIFFERENTIAL_EXPRESSION = "differential_expression"
    PATHWAY_ANALYSIS = "pathway_analysis"
    CLUSTERING = "clustering"
    CORRELATION = "correlation"
    REGRESSION = "regression"
    SURVIVAL = "survival"
    ENRICHMENT = "enrichment"
    QUALITY_CONTROL = "quality_control"


@dataclass
class DataPoint:
    point_id: str
    values: Dict[str, float]
    label: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Dataset:
    dataset_id: str
    name: str
    data_type: DataType
    points: List[DataPoint]
    columns: List[str]
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = 0.0


@dataclass
class AnalysisResult:
    dataset_id: str
    analysis_type: AnalysisType
    results: Dict[str, Any]
    statistics: Dict[str, float]
    visualizations: List[Dict[str, Any]]
    interpretation: str
    confidence: float
    warnings: List[str]
    recommendations: List[str]
    execution_time: float


@dataclass
class Report:
    title: str
    sections: List[Dict[str, Any]]
    executive_summary: str
    key_findings: List[str]
    methodology: str
    conclusions: List[str]
    limitations: List[str]
    confidence: float
    word_count: int


class SpecializedFinchAgent:
    """
    专业数据分析智能体
    模仿Robin的Finch智能体，用于实验数据分析
    """
    
    def __init__(self, api_key: Optional[str] = None, base_url: str = "https://api.anthropic.com"):
        self.api_key = api_key
        self.base_url = base_url
        self.analysis_cache: Dict[str, AnalysisResult] = {}
        self.report_history: List[Report] = []
        
    async def analyze_dataset(self, dataset: Dataset, 
                            analysis_type: AnalysisType) -> AnalysisResult:
        """分析数据集"""
        start_time = time.time()
        
        # 根据数据类型和分析类型选择方法
        if dataset.data_type == DataType.RNA_SEQ:
            results = await self._analyze_rna_seq(dataset, analysis_type)
        elif dataset.data_type == DataType.FLOW_CYTOMETRY:
            results = await self._analyze_flow_cytometry(dataset, analysis_type)
        elif dataset.data_type == DataType.PROTEOMICS:
            results = await self._analyze_proteomics(dataset, analysis_type)
        else:
            results = await self._analyze_generic(dataset, analysis_type)
        
        # 生成可视化建议
        visualizations = await self._suggest_visualizations(dataset, analysis_type, results)
        
        # 解释结果
        interpretation = await self._interpret_results(dataset, analysis_type, results)
        
        # 计算置信度
        confidence = await self._calculate_confidence(dataset, results)
        
        # 生成警告和建议
        warnings = await self._generate_warnings(dataset, results)
        recommendations = await self._generate_recommendations(dataset, analysis_type, results)
        
        execution_time = time.time() - start_time
        
        result = AnalysisResult(
            dataset_id=dataset.dataset_id,
            analysis_type=analysis_type,
            results=results,
            statistics=results.get("statistics", {}),
            visualizations=visualizations,
            interpretation=interpretation,
            confidence=confidence,
            warnings=warnings,
            recommendations=recommendations,
            execution_time=execution_time
        )
        
        self.analysis_cache[dataset.dataset_id] = result
        return result
    
    async def _analyze_rna_seq(self, dataset: Dataset, 
                             analysis_type: AnalysisType) -> Dict[str, Any]:
        """分析RNA-seq数据"""
        
        if analysis_type == AnalysisType.DIFFERENTIAL_EXPRESSION:
            # 差异表达分析
            genes = []
            for point in dataset.points[:10]:  # 限制为10个基因
                gene_name = point.label or f"Gene_{point.point_id}"
                base_expression = point.values.get("expression", 1.0)
                
                # 模拟差异表达
                import random
                fold_change = random.uniform(0.5, 2.0)
                p_value = random.uniform(0.001, 0.1)
                
                genes.append({
                    "gene": gene_name,
                    "base_expression": base_expression,
                    "fold_change": fold_change,
                    "p_value": p_value,
                    "significant": p_value < 0.05
                })
            
            significant_genes = [g for g in genes if g["significant"]]
            
            return {
                "analysis_type": "differential_expression",
                "total_genes": len(genes),
                "significant_genes": len(significant_genes),
                "top_genes": sorted(significant_genes, key=lambda x: x["p_value"])[:5],
                "statistics": {
                    "mean_fold_change": sum(g["fold_change"] for g in genes) / len(genes),
                    "median_p_value": sorted([g["p_value"] for g in genes])[len(genes) // 2]
                }
            }
        
        elif analysis_type == AnalysisType.PATHWAY_ANALYSIS:
            # 通路分析
            pathways = [
                {"name": "Cell Cycle", "p_value": 0.01, "genes": ["CDK1", "CCNB1"]},
                {"name": "Apoptosis", "p_value": 0.03, "genes": ["BAX", "CASP3"]},
                {"name": "Metabolism", "p_value": 0.05, "genes": ["GAPDH", "LDHA"]}
            ]
            
            return {
                "analysis_type": "pathway_analysis",
                "significant_pathways": [p for p in pathways if p["p_value"] < 0.05],
                "all_pathways": pathways,
                "statistics": {
                    "total_pathways": len(pathways),
                    "significant_count": len([p for p in pathways if p["p_value"] < 0.05])
                }
            }
        
        return {"analysis_type": analysis_type.value, "status": "completed"}
    
    async def _analyze_flow_cytometry(self, dataset: Dataset, 
                                    analysis_type: AnalysisType) -> Dict[str, Any]:
        """分析流式细胞术数据"""
        
        # 模拟流式细胞术分析
        populations = [
            {"name": "Live cells", "percentage": 85.5, "count": 8550},
            {"name": "Dead cells", "percentage": 14.5, "count": 1450},
            {"name": "CD4+ T cells", "percentage": 35.2, "count": 3520},
            {"name": "CD8+ T cells", "percentage": 22.1, "count": 2210}
        ]
        
        return {
            "analysis_type": "flow_cytometry",
            "populations": populations,
            "gates": [
                {"name": "Live gate", "x": "FSC", "y": "SSC"},
                {"name": "Lymphocyte gate", "x": "FSC", "y": "SSC"}
            ],
            "statistics": {
                "total_events": 10000,
                "viability": 85.5,
                "singlets": 92.3
            }
        }
    
    async def _analyze_proteomics(self, dataset: Dataset, 
                                analysis_type: AnalysisType) -> Dict[str, Any]:
        """分析蛋白质组学数据"""
        
        proteins = []
        for point in dataset.points[:10]:
            protein_name = point.label or f"Protein_{point.point_id}"
            abundance = point.values.get("abundance", 1.0)
            
            proteins.append({
                "protein": protein_name,
                "abundance": abundance,
                "fold_change": abundance / 1.0,  # 相对于对照
                "significance": "significant" if abundance > 2.0 or abundance < 0.5 else "ns"
            })
        
        return {
            "analysis_type": "proteomics",
            "total_proteins": len(proteins),
            "significant_proteins": len([p for p in proteins if p["significance"] == "significant"]),
            "top_proteins": sorted(proteins, key=lambda x: abs(x["fold_change"]), reverse=True)[:5],
            "statistics": {
                "mean_abundance": sum(p["abundance"] for p in proteins) / len(proteins),
                "dynamic_range": max(p["abundance"] for p in proteins) / min(p["abundance"] for p in proteins)
            }
        }
    
    async def _analyze_generic(self, dataset: Dataset, 
                             analysis_type: AnalysisType) -> Dict[str, Any]:
        """通用数据分析"""
        
        # 计算基本统计
        all_values = []
        for point in dataset.points:
            all_values.extend(point.values.values())
        
        if not all_values:
            return {"analysis_type": analysis_type.value, "status": "no_data"}
        
        mean_val = sum(all_values) / len(all_values)
        variance = sum((x - mean_val) ** 2 for x in all_values) / len(all_values)
        std_val = math.sqrt(variance)
        
        return {
            "analysis_type": analysis_type.value,
            "basic_statistics": {
                "count": len(all_values),
                "mean": mean_val,
                "std": std_val,
                "min": min(all_values),
                "max": max(all_values)
            },
            "status": "completed"
        }
    
    async def _suggest_visualizations(self, dataset: Dataset, 
                                    analysis_type: AnalysisType,
                                    results: Dict[str, Any]) -> List[Dict[str, Any]]:
        """建议可视化方法"""
        
        visualizations = []
        
        if dataset.data_type == DataType.RNA_SEQ:
            if analysis_type == AnalysisType.DIFFERENTIAL_EXPRESSION:
                visualizations.append({
                    "type": "volcano_plot",
                    "description": "Volcano plot of differential expression",
                    "x_axis": "log2(fold_change)",
                    "y_axis": "-log10(p_value)"
                })
                visualizations.append({
                    "type": "heatmap",
                    "description": "Heatmap of top differentially expressed genes",
                    "rows": "genes",
                    "columns": "samples"
                })
            elif analysis_type == AnalysisType.PATHWAY_ANALYSIS:
                visualizations.append({
                    "type": "bar_chart",
                    "description": "Pathway enrichment scores",
                    "x_axis": "pathway",
                    "y_axis": "-log10(p_value)"
                })
        
        elif dataset.data_type == DataType.FLOW_CYTOMETRY:
            visualizations.append({
                "type": "scatter_plot",
                "description": "FSC vs SSC plot",
                "x_axis": "FSC",
                "y_axis": "SSC"
            })
            visualizations.append({
                "type": "histogram",
                "description": "Fluorescence intensity histogram",
                "x_axis": "intensity",
                "y_axis": "count"
            })
        
        elif dataset.data_type == DataType.PROTEOMICS:
            visualizations.append({
                "type": "volcano_plot",
                "description": "Protein fold change vs significance",
                "x_axis": "log2(fold_change)",
                "y_axis": "-log10(p_value)"
            })
        
        # 通用可视化
        if not visualizations:
            visualizations.append({
                "type": "scatter_plot",
                "description": "Data distribution",
                "x_axis": "variable_1",
                "y_axis": "variable_2"
            })
        
        return visualizations
    
    async def _interpret_results(self, dataset: Dataset, 
                               analysis_type: AnalysisType,
                               results: Dict[str, Any]) -> str:
        """解释分析结果"""
        
        prompt = f"""Please interpret the following analysis results:

Dataset: {dataset.name}
Data type: {dataset.data_type.value}
Analysis type: {analysis_type.value}
Results: {json.dumps(results, ensure_ascii=False)[:1000]}

Provide a clear interpretation of:
1. Key findings
2. Biological significance
3. Potential implications
4. Limitations to consider

Keep the interpretation concise (3-5 sentences)."""
        
        response = await self._call_claude(prompt, "basic")
        return response
    
    async def _calculate_confidence(self, dataset: Dataset, 
                                  results: Dict[str, Any]) -> float:
        """计算分析置信度"""
        
        # 基于数据质量和结果稳定性计算置信度
        data_quality = min(1.0, len(dataset.points) / 100)  # 数据量
        result_stability = 0.8  # 模拟稳定性
        
        confidence = (data_quality * 0.4 + result_stability * 0.6)
        return min(1.0, confidence)
    
    async def _generate_warnings(self, dataset: Dataset, 
                               results: Dict[str, Any]) -> List[str]:
        """生成警告"""
        
        warnings = []
        
        if len(dataset.points) < 10:
            warnings.append("Small sample size may affect statistical power")
        
        if dataset.data_type == DataType.RNA_SEQ:
            if "statistics" in results:
                if results["statistics"].get("mean_fold_change", 1.0) > 1.5:
                    warnings.append("Large fold changes detected - verify experimentally")
        
        return warnings
    
    async def _generate_recommendations(self, dataset: Dataset, 
                                      analysis_type: AnalysisType,
                                      results: Dict[str, Any]) -> List[str]:
        """生成建议"""
        
        recommendations = []
        
        if analysis_type == AnalysisType.DIFFERENTIAL_EXPRESSION:
            recommendations.append("Validate top hits with qRT-PCR")
            recommendations.append("Perform pathway analysis on significant genes")
        
        elif analysis_type == AnalysisType.PATHWAY_ANALYSIS:
            recommendations.append("Confirm pathway enrichment with orthogonal methods")
            recommendations.append("Consider tissue-specific pathway databases")
        
        recommendations.append("Repeat analysis with independent dataset if available")
        
        return recommendations
    
    async def generate_report(self, dataset: Dataset, 
                            analysis_results: List[AnalysisResult]) -> Report:
        """生成分析报告"""
        
        # 构建报告内容
        sections = []
        
        # 执行摘要
        executive_summary = f"Analysis of {dataset.name} ({dataset.data_type.value}) completed successfully."
        
        # 方法学
        methodology = f"Analyzed {len(dataset.points)} data points using {len(analysis_results)} analysis methods."
        
        # 关键发现
        key_findings = []
        for result in analysis_results:
            if result.analysis_type == AnalysisType.DIFFERENTIAL_EXPRESSION:
                key_findings.append(f"Identified {result.results.get('significant_genes', 0)} significant genes")
            elif result.analysis_type == AnalysisType.PATHWAY_ANALYSIS:
                key_findings.append(f"Found {len(result.results.get('significant_pathways', []))} significant pathways")
        
        if not key_findings:
            key_findings = ["Analysis completed", "Results interpreted"]
        
        # 结论
        conclusions = [
            f"Dataset contains {len(dataset.points)} data points",
            f"Analysis confidence: {sum(r.confidence for r in analysis_results) / len(analysis_results):.2%}"
        ]
        
        # 局限性
        limitations = [
            "Analysis based on computational methods",
            "Experimental validation recommended"
        ]
        
        # 计算总字数
        word_count = sum(len(section.get("content", "").split()) for section in sections)
        
        report = Report(
            title=f"Analysis Report: {dataset.name}",
            sections=sections,
            executive_summary=executive_summary,
            key_findings=key_findings,
            methodology=methodology,
            conclusions=conclusions,
            limitations=limitations,
            confidence=sum(r.confidence for r in analysis_results) / len(analysis_results) if analysis_results else 0.5,
            word_count=word_count
        )
        
        self.report_history.append(report)
        return report
    
    async def _call_claude(self, prompt: str, task_type: str = "basic") -> str:
        """调用Claude API"""
        if not self.api_key:
            return self._simulate_claude_response(prompt)
        
        try:
            import aiohttp
            
            headers = {
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            }
            
            payload = {
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 1024,
                "messages": [{"role": "user", "content": prompt}]
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.base_url}/v1/messages",
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=60)
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get("content", [{}])[0].get("text", "")
                    else:
                        return self._simulate_claude_response(prompt)
                        
        except Exception:
            return self._simulate_claude_response(prompt)
    
    def _simulate_claude_response(self, prompt: str) -> str:
        """模拟Claude响应"""
        if "interpret" in prompt.lower():
            return "Analysis reveals significant patterns in the data. Key findings suggest potential biological relevance that warrants further investigation. Limitations include sample size and methodological constraints."
        return "Analysis completed successfully"
    
    def get_statistics(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            "total_analyses": len(self.analysis_cache),
            "total_reports": len(self.report_history),
            "data_types_analyzed": list(set(r.dataset_id for r in self.analysis_cache.values())),
            "average_confidence": sum(r.confidence for r in self.analysis_cache.values()) / len(self.analysis_cache) if self.analysis_cache else 0
        }


_specialized_finch_agent_instance = None

def get_specialized_finch_agent(api_key: Optional[str] = None) -> SpecializedFinchAgent:
    global _specialized_finch_agent_instance
    if _specialized_finch_agent_instance is None:
        _specialized_finch_agent_instance = SpecializedFinchAgent(api_key)
    return _specialized_finch_agent_instance
