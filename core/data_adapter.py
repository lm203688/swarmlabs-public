"""
Data Adapter for External Data Source Integration
Inspired by Robin's data integration capabilities
Features:
  - Multi-source data integration
  - OmicsDI, Figshare, GEO integration
  - Data normalization
  - Format conversion
  - Caching
"""

from enum import Enum
from typing import Dict, Any, List, Optional, Union
import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import datetime


class DataSource(Enum):
    OMICSDI = "omicsdi"
    FIGSHARE = "figshare"
    GEO = "geo"
    ENA = "ena"
    PRIDE = "pride"
    UNIPROT = "uniprot"
    CUSTOM = "custom"


class DataType(Enum):
    TRANSCRIPTOMICS = "transcriptomics"
    PROTEOMICS = "proteomics"
    GENOMICS = "genomics"
    METABOLOMICS = "metabolomics"
    EPIGENOMICS = "epigenomics"
    CLINICAL = "clinical"
    GENERIC = "generic"


@dataclass
class DataQuery:
    query: str
    source: DataSource
    data_type: DataType = DataType.GENERIC
    max_results: int = 100
    filters: Dict[str, Any] = field(default_factory=dict)
    format: str = "json"


@dataclass
class DataRecord:
    record_id: str
    source: DataSource
    data_type: DataType
    content: Dict[str, Any]
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = 0.0
    size_bytes: int = 0


@dataclass
class DataResultSet:
    query: DataQuery
    records: List[DataRecord]
    total_count: int
    retrieval_time: float
    cache_hit: bool = False


@dataclass
class NormalizedData:
    original_source: DataSource
    normalized_fields: Dict[str, Any]
    quality_score: float
    warnings: List[str]


class DataAdapter:
    """
    数据适配器
    整合外部数据源，如OmicsDI、Figshare、GEO等
    """
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key
        self.cache: Dict[str, DataResultSet] = {}
        self.cache_ttl = 3600  # 1 hour
        self.stats = {
            "queries": 0,
            "cache_hits": 0,
            "records_retrieved": 0
        }
    
    async def query(self, data_query: DataQuery) -> DataResultSet:
        """执行数据查询"""
        start_time = time.time()
        self.stats["queries"] += 1
        
        # 检查缓存
        cache_key = self._get_cache_key(data_query)
        if cache_key in self.cache:
            cached = self.cache[cache_key]
            if time.time() - cached.records[0].timestamp if cached.records else 0 < self.cache_ttl:
                self.stats["cache_hits"] += 1
                cached.cache_hit = True
                return cached
        
        # 根据数据源执行查询
        if data_query.source == DataSource.OMICSDI:
            records = await self._query_omicsdi(data_query)
        elif data_query.source == DataSource.FIGSHARE:
            records = await self._query_figshare(data_query)
        elif data_query.source == DataSource.GEO:
            records = await self._query_geo(data_query)
        else:
            records = await self._query_generic(data_query)
        
        retrieval_time = time.time() - start_time
        
        result_set = DataResultSet(
            query=data_query,
            records=records,
            total_count=len(records),
            retrieval_time=retrieval_time
        )
        
        # 缓存结果
        self.cache[cache_key] = result_set
        self.stats["records_retrieved"] += len(records)
        
        return result_set
    
    async def _query_omicsdi(self, query: DataQuery) -> List[DataRecord]:
        """OmicsDI查询 (模拟)"""
        await asyncio.sleep(0.1)
        
        records = []
        for i in range(min(10, query.max_results)):
            record = DataRecord(
                record_id=f"omicsdi_{i}",
                source=DataSource.OMICSDI,
                data_type=query.data_type,
                content={
                    "dataset_id": f"ODS{i:06d}",
                    "title": f"Dataset {i} from OmicsDI",
                    "description": f"Description for dataset {i}",
                    "organism": "Homo sapiens",
                    "technology": "RNA-seq",
                    "sample_count": 10 + i
                },
                metadata={"repository": "omicsdi.org"},
                timestamp=time.time(),
                size_bytes=1024 * (i + 1)
            )
            records.append(record)
        
        return records
    
    async def _query_figshare(self, query: DataQuery) -> List[DataRecord]:
        """Figshare查询 (模拟)"""
        await asyncio.sleep(0.1)
        
        records = []
        for i in range(min(8, query.max_results)):
            record = DataRecord(
                record_id=f"figshare_{i}",
                source=DataSource.FIGSHARE,
                data_type=query.data_type,
                content={
                    "item_id": f"FS{i:06d}",
                    "title": f"Research Data {i} from Figshare",
                    "description": f"Description for item {i}",
                    "authors": ["Author 1", "Author 2"],
                    "categories": ["Genetics", "Genomics"],
                    "file_count": 5 + i
                },
                metadata={"repository": "figshare.com"},
                timestamp=time.time(),
                size_bytes=2048 * (i + 1)
            )
            records.append(record)
        
        return records
    
    async def _query_geo(self, query: DataQuery) -> List[DataRecord]:
        """GEO查询 (模拟)"""
        await asyncio.sleep(0.1)
        
        records = []
        for i in range(min(12, query.max_results)):
            record = DataRecord(
                record_id=f"geo_{i}",
                source=DataSource.GEO,
                data_type=query.data_type,
                content={
                    "accession": f"GSE{100000 + i}",
                    "title": f"GEO Series {i}",
                    "summary": f"Summary for series {i}",
                    "platform": "GPL21185",
                    "sample_count": 20 + i,
                    "series_type": "Expression profiling by high throughput sequencing"
                },
                metadata={"repository": "ncbi.nlm.nih.gov/geo"},
                timestamp=time.time(),
                size_bytes=4096 * (i + 1)
            )
            records.append(record)
        
        return records
    
    async def _query_generic(self, query: DataQuery) -> List[DataRecord]:
        """通用查询"""
        records = []
        for i in range(min(5, query.max_results)):
            record = DataRecord(
                record_id=f"generic_{i}",
                source=query.source,
                data_type=query.data_type,
                content={
                    "id": f"GEN{i:06d}",
                    "title": f"Data Record {i}",
                    "description": f"Description {i}"
                },
                timestamp=time.time(),
                size_bytes=512 * (i + 1)
            )
            records.append(record)
        
        return records
    
    async def normalize_data(self, record: DataRecord) -> NormalizedData:
        """数据标准化"""
        
        normalized_fields = {}
        warnings = []
        
        # 标准化字段
        if record.data_type == DataType.TRANSCRIPTOMICS:
            normalized_fields = self._normalize_transcriptomics(record)
        elif record.data_type == DataType.PROTEOMICS:
            normalized_fields = self._normalize_proteomics(record)
        else:
            normalized_fields = self._normalize_generic(record)
        
        # 计算质量分数
        quality_score = self._calculate_quality_score(record, normalized_fields)
        
        return NormalizedData(
            original_source=record.source,
            normalized_fields=normalized_fields,
            quality_score=quality_score,
            warnings=warnings
        )
    
    def _normalize_transcriptomics(self, record: DataRecord) -> Dict[str, Any]:
        """标准化转录组数据"""
        content = record.content
        return {
            "dataset_id": content.get("accession", content.get("dataset_id", "")),
            "title": content.get("title", ""),
            "organism": content.get("organism", "unknown"),
            "technology": content.get("technology", "RNA-seq"),
            "sample_count": content.get("sample_count", 0),
            "platform": content.get("platform", "unknown")
        }
    
    def _normalize_proteomics(self, record: DataRecord) -> Dict[str, Any]:
        """标准化蛋白质组数据"""
        content = record.content
        return {
            "dataset_id": content.get("item_id", content.get("dataset_id", "")),
            "title": content.get("title", ""),
            "authors": content.get("authors", []),
            "categories": content.get("categories", []),
            "file_count": content.get("file_count", 0)
        }
    
    def _normalize_generic(self, record: DataRecord) -> Dict[str, Any]:
        """通用标准化"""
        content = record.content
        return {
            "id": content.get("id", content.get("dataset_id", "")),
            "title": content.get("title", ""),
            "description": content.get("description", "")
        }
    
    def _calculate_quality_score(self, record: DataRecord, 
                               normalized: Dict[str, Any]) -> float:
        """计算数据质量分数"""
        score = 0.5  # 基础分
        
        # 完整性检查
        required_fields = ["title", "description"]
        for field in required_fields:
            if normalized.get(field):
                score += 0.1
        
        # 元数据丰富度
        if record.metadata:
            score += 0.1
        
        # 数据大小
        if record.size_bytes > 1024:
            score += 0.1
        
        return min(1.0, score)
    
    async def batch_query(self, queries: List[DataQuery]) -> List[DataResultSet]:
        """批量查询"""
        tasks = [self.query(q) for q in queries]
        return await asyncio.gather(*tasks)
    
    async def get_data_summary(self, record: DataRecord) -> Dict[str, Any]:
        """获取数据摘要"""
        return {
            "record_id": record.record_id,
            "source": record.source.value,
            "data_type": record.data_type.value,
            "title": record.content.get("title", "Unknown"),
            "size_bytes": record.size_bytes,
            "timestamp": record.timestamp
        }
    
    def get_statistics(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            "total_queries": self.stats["queries"],
            "cache_hits": self.stats["cache_hits"],
            "cache_hit_rate": self.stats["cache_hits"] / self.stats["queries"] if self.stats["queries"] > 0 else 0,
            "total_records": self.stats["records_retrieved"],
            "cache_size": len(self.cache)
        }
    
    def _get_cache_key(self, query: DataQuery) -> str:
        """生成缓存键"""
        import hashlib
        key_data = f"{query.source.value}:{query.query}:{query.data_type.value}"
        return hashlib.md5(key_data.encode()).hexdigest()


_data_adapter_instance = None

def get_data_adapter(api_key: Optional[str] = None) -> DataAdapter:
    global _data_adapter_instance
    if _data_adapter_instance is None:
        _data_adapter_instance = DataAdapter(api_key)
    return _data_adapter_instance
