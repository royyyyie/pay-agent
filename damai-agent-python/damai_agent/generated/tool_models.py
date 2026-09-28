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

class PurchaseIntentPrepareRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', populate_by_name=True)
    programId: int = Field(..., ge=1, description='已由实时节目工具确认的节目 ID，也是购买意向分片键')
    ticketCategoryId: int = Field(..., ge=1, description='必须属于 programId 且由实时票档工具确认')
    quantity: int = Field(..., ge=1, le=6, description='购票数量；价格和库存由 Java 重新读取，模型不得提供金额')
    ticketUserIds: List[int] = Field(..., min_length=1, max_length=6, description='只能使用 list_purchase_attendees 返回的 Java 管理引用，数量必须与 quantity 相同')

class PurchaseAttendeeListRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', populate_by_name=True)
    pass

class PurchaseAttendeeRef(BaseModel):
    model_config = ConfigDict(extra='forbid', populate_by_name=True)
    ticketUserId: int = Field(..., ge=1, description='Java 用户服务管理的不透明购票人引用')
    displayLabel: str = Field(..., min_length=1, max_length=64, description='仅用于确认选择的脱敏标签')

class PurchaseIntentGetRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', populate_by_name=True)
    intentId: int = Field(..., ge=1)
    programId: int = Field(..., ge=1, description='创建意向时返回的不可变分片键')

class PurchaseIntentCancelRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', populate_by_name=True)
    intentId: int = Field(..., ge=1)
    programId: int = Field(..., ge=1, description='创建意向时返回的不可变分片键')
    expectedVersion: int = Field(..., ge=1, description='乐观锁版本；冲突后必须重新查询')

class PurchaseOrderSubmitRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', populate_by_name=True)
    intentId: int = Field(..., ge=1)
    programId: int = Field(..., ge=1, description='创建意向时返回的不可变分片键')
    expectedVersion: int = Field(..., ge=1, description='必须是 CONFIRMED 意向的当前版本；凭证在 Java 侧消费')

class PurchaseIntent(BaseModel):
    model_config = ConfigDict(extra='allow', populate_by_name=True)
    intentId: int = Field(...)
    programId: int = Field(...)
    ticketCategoryId: int = Field(...)
    quantity: int = Field(..., ge=1, le=6)
    ticketUserIds: List[int] = Field(..., min_length=0, max_length=6, description='已绑定到报价摘要的 Java 管理购票人引用')
    unitAmountFen: int = Field(..., ge=0, description='Java 从实时票档价格生成的单价，单位为人民币分')
    totalAmountFen: int = Field(..., ge=0, description='Java 计算的总金额，单位为人民币分')
    currency: Literal['CNY'] = Field(...)
    quoteHash: str = Field(..., pattern='^[0-9a-f]{64}$', description='绑定归属、节目、票档、数量、金额和有效期的报价摘要，不是确认凭据')
    quoteExpiresAt: datetime = Field(...)
    intentStatus: Literal['PENDING_CONFIRMATION', 'CONFIRMED', 'CANCELLED', 'EXPIRED', 'SUBMITTING', 'SUBMITTED', 'SUBMISSION_FAILED', 'SUBMISSION_UNKNOWN'] = Field(...)
    version: int = Field(..., ge=1)
    createdAt: datetime = Field(...)
    updatedAt: datetime = Field(...)
    orderNumber: Optional[int] = Field(None, ge=1, description='Java 订单状态机生成并持久化的稳定订单号；为空表示尚未受理提交')
    submittedAt: Optional[datetime] = Field(None, description='订单服务事实被确认的时间')

class WatchRuleCreateRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', populate_by_name=True)
    programId: int = Field(..., ge=1, description='已由节目查询确认的节目 ID，也是监控规则分片键')
    name: str = Field(cast(Any, None), min_length=1, max_length=100, description='用户可识别的规则名称')
    ticketCategoryIds: List[int] = Field(cast(Any, None), max_length=20, description='可选票档白名单；空集合表示监控节目下全部票档')
    maxPrice: float = Field(cast(Any, None), ge=0, le=999999999.99, description='只在可售价格不高于该值时触发；不设置表示不限价格')
    minRemaining: int = Field(1, ge=1, le=1000000, description='满足条件的票档最少剩余数量')
    checkIntervalSeconds: int = Field(300, ge=30, le=86400, description='Java 可靠调度器的目标检查间隔，不承诺精确触发时刻')
    notificationChannel: Literal['IN_APP'] = Field('IN_APP', description='阶段 5 首批仅开放站内通知')

class WatchRuleUpdateRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', populate_by_name=True)
    ruleId: int = Field(..., ge=1)
    programId: int = Field(..., ge=1, description='从 list_watch_rules 获得的不可变分片键')
    expectedVersion: int = Field(..., ge=1, description='乐观锁版本；冲突后必须重新查询，禁止盲目覆盖')
    name: str = Field(cast(Any, None), min_length=1, max_length=100)
    ticketCategoryIds: List[int] = Field(cast(Any, None), max_length=20)
    maxPrice: float = Field(cast(Any, None), ge=0, le=999999999.99)
    clearMaxPrice: bool = Field(False, description='显式移除价格上限；不能与 maxPrice 同时为真')
    minRemaining: int = Field(cast(Any, None), ge=1, le=1000000)
    checkIntervalSeconds: int = Field(cast(Any, None), ge=30, le=86400)

class WatchRuleStatusRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', populate_by_name=True)
    ruleId: int = Field(..., ge=1)
    programId: int = Field(..., ge=1, description='从 list_watch_rules 获得的不可变分片键')
    expectedVersion: int = Field(..., ge=1)
    targetStatus: Literal['ACTIVE', 'PAUSED'] = Field(...)

class WatchRuleListRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', populate_by_name=True)
    ruleStatus: Literal['ACTIVE', 'PAUSED'] = Field(cast(Any, None))
    pageNumber: int = Field(1, ge=1, le=1000)
    pageSize: int = Field(10, ge=1, le=20)

class WatchRule(BaseModel):
    model_config = ConfigDict(extra='allow', populate_by_name=True)
    ruleId: int = Field(...)
    programId: int = Field(...)
    name: str = Field(cast(Any, None))
    ticketCategoryIds: List[int] = Field(cast(Any, None))
    maxPrice: float = Field(cast(Any, None))
    minRemaining: int = Field(...)
    checkIntervalSeconds: int = Field(...)
    notificationChannel: Literal['IN_APP'] = Field(...)
    ruleStatus: Literal['ACTIVE', 'PAUSED'] = Field(...)
    version: int = Field(..., ge=1)
    nextCheckAt: str = Field(...)
    lastCheckedAt: str = Field(cast(Any, None))
    lastTriggeredAt: str = Field(cast(Any, None))
    createdAt: str = Field(...)
    updatedAt: str = Field(...)

class WatchRulePage(BaseModel):
    model_config = ConfigDict(extra='allow', populate_by_name=True)
    pageNumber: int = Field(...)
    pageSize: int = Field(...)
    totalSize: int = Field(...)
    list: List[WatchRule] = Field(..., max_length=20)

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

class WatchRuleResponse(BaseModel):
    model_config = ConfigDict(extra='allow', populate_by_name=True)
    requestId: str = Field(...)
    success: bool = Field(...)
    code: int = Field(...)
    message: str = Field(...)
    data: WatchRule = Field(...)
    retryable: bool = Field(...)
    freshnessAt: datetime = Field(...)

class PurchaseIntentResponse(BaseModel):
    model_config = ConfigDict(extra='allow', populate_by_name=True)
    requestId: str = Field(...)
    success: bool = Field(...)
    code: int = Field(...)
    message: str = Field(...)
    data: PurchaseIntent = Field(...)
    retryable: bool = Field(...)
    freshnessAt: datetime = Field(...)

class PurchaseAttendeeListResponse(BaseModel):
    model_config = ConfigDict(extra='allow', populate_by_name=True)
    requestId: str = Field(...)
    success: bool = Field(...)
    code: int = Field(...)
    message: str = Field(...)
    data: List[PurchaseAttendeeRef] = Field(..., max_length=20)
    retryable: bool = Field(...)
    freshnessAt: datetime = Field(...)

class WatchRulePageResponse(BaseModel):
    model_config = ConfigDict(extra='allow', populate_by_name=True)
    requestId: str = Field(...)
    success: bool = Field(...)
    code: int = Field(...)
    message: str = Field(...)
    data: WatchRulePage = Field(...)
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

GENERATED_MODELS = (ProgramSearchRequest, ProgramIdRequest, ProgramRecommendationRequest, PurchaseIntentPrepareRequest, PurchaseAttendeeListRequest, PurchaseAttendeeRef, PurchaseIntentGetRequest, PurchaseIntentCancelRequest, PurchaseOrderSubmitRequest, PurchaseIntent, WatchRuleCreateRequest, WatchRuleUpdateRequest, WatchRuleStatusRequest, WatchRuleListRequest, WatchRule, WatchRulePage, ToolResponse, ProgramSearchResponse, WatchRuleResponse, PurchaseIntentResponse, PurchaseAttendeeListResponse, WatchRulePageResponse, ProgramPage, ProgramSummary, ProgramRecommendationCandidate, ProgramRecommendationPage, ProgramDetail, TicketCategory, ProgramRecommendationResponse, GetProgramDetailResponse, ListTicketCategoriesResponse,)
for _model in GENERATED_MODELS:
    _model.model_rebuild()

REQUEST_MODELS: Dict[str, type[BaseModel]] = {
    'get_program_detail': ProgramIdRequest,
    'recommend_programs': ProgramRecommendationRequest,
    'search_programs': ProgramSearchRequest,
    'list_ticket_categories': ProgramIdRequest,
    'list_purchase_attendees': PurchaseAttendeeListRequest,
    'cancel_purchase_intent': PurchaseIntentCancelRequest,
    'get_purchase_intent': PurchaseIntentGetRequest,
    'prepare_purchase_intent': PurchaseIntentPrepareRequest,
    'submit_confirmed_order': PurchaseOrderSubmitRequest,
    'create_watch_rule': WatchRuleCreateRequest,
    'list_watch_rules': WatchRuleListRequest,
    'set_watch_rule_status': WatchRuleStatusRequest,
    'update_watch_rule': WatchRuleUpdateRequest,
}

RESPONSE_MODELS: Dict[str, type[BaseModel]] = {
    'get_program_detail': GetProgramDetailResponse,
    'recommend_programs': ProgramRecommendationResponse,
    'search_programs': ProgramSearchResponse,
    'list_ticket_categories': ListTicketCategoriesResponse,
    'list_purchase_attendees': PurchaseAttendeeListResponse,
    'cancel_purchase_intent': PurchaseIntentResponse,
    'get_purchase_intent': PurchaseIntentResponse,
    'prepare_purchase_intent': PurchaseIntentResponse,
    'submit_confirmed_order': PurchaseIntentResponse,
    'create_watch_rule': WatchRuleResponse,
    'list_watch_rules': WatchRulePageResponse,
    'set_watch_rule_status': WatchRuleResponse,
    'update_watch_rule': WatchRuleResponse,
}
