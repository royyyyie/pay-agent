"""Generated Pydantic models from contracts/agent-tools-v1.openapi.yaml; do not edit."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional, cast

from pydantic import BaseModel, ConfigDict, Field

class ProgramSearchRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', populate_by_name=True)
    keyword: str = Field(cast(Any, None), max_length=100, description='节目名或艺人关键词')
    areaId: int = Field(cast(Any, None), description='城市或区域 ID')
    parentProgramCategoryId: int = Field(cast(Any, None), description='父节目分类 ID')
    programCategoryId: int = Field(cast(Any, None), description='节目分类 ID')
    maxPrice: float = Field(cast(Any, None), ge=0, le=999999999.99, description='推荐预算硬上限；Java 会移除最低票价超过该值的候选节目')
    timeType: Literal[0, 1, 2, 3, 4, 5] = Field(0, description='0 全部、1 今天、2 明天、3 一周内、4 一月内、5 自定义')
    startDateTime: str = Field(cast(Any, None), description='自定义开始时间，格式 yyyy-MM-dd HH:mm:ss')
    endDateTime: str = Field(cast(Any, None), description='自定义结束时间，格式 yyyy-MM-dd HH:mm:ss')
    sortType: Literal[1, 2, 3, 4] = Field(1, description='1 相关度、2 推荐、3 最近开场、4 最新上架')
    pageNumber: int = Field(1, ge=1, description='页码，从 1 开始')
    pageSize: int = Field(10, ge=1, le=20, description='每页条数，最多 20')

class ProgramIdRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', populate_by_name=True)
    programId: int = Field(..., description='从节目搜索结果获得的节目 ID')

class ProgramRecommendationRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', populate_by_name=True)
    keyword: str = Field(cast(Any, None), max_length=100, description='节目名或艺人关键词')
    areaId: int = Field(cast(Any, None), description='城市或区域 ID')
    parentProgramCategoryId: int = Field(cast(Any, None), description='父节目分类 ID')
    programCategoryId: int = Field(cast(Any, None), description='节目分类 ID')
    maxPrice: float = Field(cast(Any, None), ge=0, le=999999999.99, description='推荐预算硬上限；Java 会再次按实时可售票档执行过滤')
    timeType: Literal[0, 1, 2, 3, 4, 5] = Field(0, description='0 全部、1 今天、2 明天、3 一周内、4 一月内、5 自定义')
    startDateTime: str = Field(cast(Any, None), description='自定义开始时间，格式 yyyy-MM-dd HH:mm:ss')
    endDateTime: str = Field(cast(Any, None), description='自定义结束时间，格式 yyyy-MM-dd HH:mm:ss')
    sortType: Literal[1, 2, 3, 4] = Field(1, description='搜索阶段排序：1 相关度、2 推荐、3 最近开场、4 最新上架')
    pageNumber: int = Field(1, ge=1, description='推荐服务固定从第一页开始扫描，此字段仅保留契约兼容性')
    pageSize: int = Field(10, ge=1, le=20, description='推荐服务使用有界扫描窗口，此字段仅保留契约兼容性')
    preference: Literal['RELEVANCE', 'LOWEST_PRICE', 'EARLIEST_SHOW', 'MOST_AVAILABLE'] = Field('RELEVANCE', description='仅在硬约束和实时余票过滤后应用的软排序偏好')
    candidateLimit: int = Field(3, ge=1, le=5, description='最多返回候选数量；服务端最多扫描前 10 个搜索结果')

class ToolResponse(BaseModel):
    model_config = ConfigDict(extra='allow', populate_by_name=True)
    requestId: str = Field(...)
    success: bool = Field(...)
    code: int = Field(...)
    message: str = Field(...)
    data: Optional[Any] = Field(None)
    retryable: bool = Field(...)
    freshnessAt: datetime = Field(...)

class ProgramSearchResponse(BaseModel):
    model_config = ConfigDict(extra='allow', populate_by_name=True)
    requestId: str = Field(...)
    success: bool = Field(...)
    code: int = Field(...)
    message: str = Field(...)
    data: ProgramPage = Field(cast(Any, None))
    retryable: bool = Field(...)
    freshnessAt: datetime = Field(...)

class ProgramPage(BaseModel):
    model_config = ConfigDict(extra='allow', populate_by_name=True)
    pageNum: int = Field(cast(Any, None))
    pageSize: int = Field(cast(Any, None))
    totalSize: int = Field(cast(Any, None))
    list: List[ProgramSummary] = Field(cast(Any, None))

class ProgramSummary(BaseModel):
    model_config = ConfigDict(extra='allow', populate_by_name=True)
    id: int = Field(cast(Any, None))
    title: str = Field(cast(Any, None))
    actor: str = Field(cast(Any, None))
    place: str = Field(cast(Any, None))
    areaName: str = Field(cast(Any, None))
    programCategoryName: str = Field(cast(Any, None))
    showTime: str = Field(cast(Any, None))
    minPrice: float = Field(cast(Any, None))
    maxPrice: float = Field(cast(Any, None))

class ProgramRecommendationCandidate(BaseModel):
    model_config = ConfigDict(extra='allow', populate_by_name=True)
    id: int = Field(cast(Any, None))
    title: str = Field(cast(Any, None))
    actor: str = Field(cast(Any, None))
    place: str = Field(cast(Any, None))
    areaName: str = Field(cast(Any, None))
    programCategoryName: str = Field(cast(Any, None))
    showTime: str = Field(cast(Any, None))
    minPrice: float = Field(cast(Any, None))
    maxPrice: float = Field(cast(Any, None))
    rank: int = Field(..., ge=1)
    availableTicketCategoryCount: int = Field(..., ge=1)
    lowestAvailablePrice: float = Field(..., ge=0)
    totalRemaining: int = Field(..., ge=1)
    reasonCodes: List[Literal['LIVE_INVENTORY_CONFIRMED', 'BUDGET_VERIFIED', 'RANKED_BY_RELEVANCE', 'RANKED_BY_LOWEST_PRICE', 'RANKED_BY_EARLIEST_SHOW', 'RANKED_BY_MOST_AVAILABLE']] = Field(..., min_length=2, max_length=3)

class ProgramRecommendationPage(BaseModel):
    model_config = ConfigDict(extra='allow', populate_by_name=True)
    scannedCount: int = Field(..., ge=0, le=10)
    eligibleCount: int = Field(..., ge=0, le=10)
    preference: Literal['RELEVANCE', 'LOWEST_PRICE', 'EARLIEST_SHOW', 'MOST_AVAILABLE'] = Field(...)
    list: List[ProgramRecommendationCandidate] = Field(..., max_length=5)

class ProgramDetail(BaseModel):
    model_config = ConfigDict(extra='allow', populate_by_name=True)
    id: int = Field(cast(Any, None))
    title: str = Field(cast(Any, None))
    actor: str = Field(cast(Any, None))
    place: str = Field(cast(Any, None))
    areaName: str = Field(cast(Any, None))
    programCategoryName: str = Field(cast(Any, None))
    showTime: str = Field(cast(Any, None))
    minPrice: float = Field(cast(Any, None))
    maxPrice: float = Field(cast(Any, None))
    importantNotice: str = Field(cast(Any, None))
    refundTicketRule: str = Field(cast(Any, None))
    entryRule: str = Field(cast(Any, None))
    childPurchase: str = Field(cast(Any, None))
    perOrderLimitPurchaseCount: int = Field(cast(Any, None))
    permitChooseSeat: int = Field(cast(Any, None))

class TicketCategory(BaseModel):
    model_config = ConfigDict(extra='allow', populate_by_name=True)
    programId: int = Field(cast(Any, None))
    introduce: str = Field(cast(Any, None))
    price: float = Field(cast(Any, None))
    totalNumber: int = Field(cast(Any, None))
    remainNumber: int = Field(cast(Any, None))

class ProgramRecommendationResponse(BaseModel):
    model_config = ConfigDict(extra='allow', populate_by_name=True)
    requestId: str = Field(...)
    success: bool = Field(...)
    code: int = Field(...)
    message: str = Field(...)
    data: ProgramRecommendationPage = Field(...)
    retryable: bool = Field(...)
    freshnessAt: datetime = Field(...)

class GetProgramDetailResponse(BaseModel):
    model_config = ConfigDict(extra='allow', populate_by_name=True)
    requestId: str = Field(...)
    success: bool = Field(...)
    code: int = Field(...)
    message: str = Field(...)
    data: ProgramDetail = Field(cast(Any, None))
    retryable: bool = Field(...)
    freshnessAt: datetime = Field(...)

class ListTicketCategoriesResponse(BaseModel):
    model_config = ConfigDict(extra='allow', populate_by_name=True)
    requestId: str = Field(...)
    success: bool = Field(...)
    code: int = Field(...)
    message: str = Field(...)
    data: List[TicketCategory] = Field(cast(Any, None))
    retryable: bool = Field(...)
    freshnessAt: datetime = Field(...)

GENERATED_MODELS = (ProgramSearchRequest, ProgramIdRequest, ProgramRecommendationRequest, ToolResponse, ProgramSearchResponse, ProgramPage, ProgramSummary, ProgramRecommendationCandidate, ProgramRecommendationPage, ProgramDetail, TicketCategory, ProgramRecommendationResponse, GetProgramDetailResponse, ListTicketCategoriesResponse,)
for _model in GENERATED_MODELS:
    _model.model_rebuild()

REQUEST_MODELS: Dict[str, type[BaseModel]] = {
    'get_program_detail': ProgramIdRequest,
    'recommend_programs': ProgramRecommendationRequest,
    'search_programs': ProgramSearchRequest,
    'list_ticket_categories': ProgramIdRequest,
}

RESPONSE_MODELS: Dict[str, type[BaseModel]] = {
    'get_program_detail': GetProgramDetailResponse,
    'recommend_programs': ProgramRecommendationResponse,
    'search_programs': ProgramSearchResponse,
    'list_ticket_categories': ListTicketCategoriesResponse,
}
