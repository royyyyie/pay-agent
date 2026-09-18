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
    timeType: Literal[0, 1, 2, 3, 4, 5] = Field(0, description='0 全部、1 今天、2 明天、3 一周内、4 一月内、5 自定义')
    startDateTime: str = Field(cast(Any, None), description='自定义开始时间，格式 yyyy-MM-dd HH:mm:ss')
    endDateTime: str = Field(cast(Any, None), description='自定义结束时间，格式 yyyy-MM-dd HH:mm:ss')
    sortType: Literal[1, 2, 3, 4] = Field(1, description='1 相关度、2 推荐、3 最近开场、4 最新上架')
    pageNumber: int = Field(1, ge=1, description='页码，从 1 开始')
    pageSize: int = Field(10, ge=1, le=20, description='每页条数，最多 20')

class ProgramIdRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', populate_by_name=True)
    programId: int = Field(..., description='从节目搜索结果获得的节目 ID')

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

GENERATED_MODELS = (ProgramSearchRequest, ProgramIdRequest, ToolResponse, ProgramSearchResponse, ProgramPage, ProgramSummary, ProgramDetail, TicketCategory, GetProgramDetailResponse, ListTicketCategoriesResponse,)
for _model in GENERATED_MODELS:
    _model.model_rebuild()

REQUEST_MODELS: Dict[str, type[BaseModel]] = {
    'get_program_detail': ProgramIdRequest,
    'search_programs': ProgramSearchRequest,
    'list_ticket_categories': ProgramIdRequest,
}

RESPONSE_MODELS: Dict[str, type[BaseModel]] = {
    'get_program_detail': GetProgramDetailResponse,
    'search_programs': ProgramSearchResponse,
    'list_ticket_categories': ListTicketCategoriesResponse,
}
