package com.damai.service.es;

import cn.hutool.core.collection.CollectionUtil;
import com.damai.core.SpringUtil;
import com.damai.dto.EsDataQueryDto;
import com.damai.dto.ProgramListDto;
import com.damai.dto.ProgramPageListDto;
import com.damai.dto.ProgramRecommendListDto;
import com.damai.dto.ProgramSearchDto;
import com.damai.enums.BusinessStatus;
import com.damai.page.PageUtil;
import com.damai.page.PageVo;
import com.damai.service.init.ProgramDocumentParamName;
import com.damai.service.tool.ProgramPageOrder;
import com.damai.util.BusinessEsHandle;
import com.damai.util.StringUtil;
import com.damai.vo.ProgramHomeVo;
import com.damai.vo.ProgramListVo;
import com.github.pagehelper.PageInfo;
import lombok.extern.slf4j.Slf4j;
import org.elasticsearch.index.query.BoolQueryBuilder;
import org.elasticsearch.index.query.MatchAllQueryBuilder;
import org.elasticsearch.index.query.QueryBuilder;
import org.elasticsearch.index.query.QueryBuilders;
import org.elasticsearch.script.Script;
import org.elasticsearch.search.builder.SearchSourceBuilder;
import org.elasticsearch.search.fetch.subphase.highlight.HighlightBuilder;
import org.elasticsearch.search.sort.FieldSortBuilder;
import org.elasticsearch.search.sort.ScriptSortBuilder;
import org.elasticsearch.search.sort.SortBuilders;
import org.elasticsearch.search.sort.SortOrder;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Objects;

/**
 * @program: 极度真实还原大麦网高并发实战项目。 添加 阿星不是程序员 微信，添加时备注 大麦 来获取项目的完整资料 
 * @description: es查询
 * @author: 阿星不是程序员
 **/
@Slf4j
@Component
public class ProgramEs {
    
    @Autowired
    private BusinessEsHandle businessEsHandle;
    
    public List<ProgramHomeVo> selectHomeList(ProgramListDto programListDto) {
        List<ProgramHomeVo> programHomeVoList = new ArrayList<>();
        
        try {
            //按照父节目id集合来进行循环从es中查询，主页页面是4个父节目，也就是循环4次
            for (Long parentProgramCategoryId : programListDto.getParentProgramCategoryIds()) {
                List<EsDataQueryDto> programEsQueryDto = new ArrayList<>();
                if (Objects.nonNull(programListDto.getAreaId())) {
                    //地区id
                    EsDataQueryDto areaIdQueryDto = new EsDataQueryDto();
                    areaIdQueryDto.setParamName(ProgramDocumentParamName.AREA_ID);
                    areaIdQueryDto.setParamValue(programListDto.getAreaId());
                    programEsQueryDto.add(areaIdQueryDto);
                }else {
                    EsDataQueryDto primeQueryDto = new EsDataQueryDto();
                    primeQueryDto.setParamName(ProgramDocumentParamName.PRIME);
                    primeQueryDto.setParamValue(BusinessStatus.YES.getCode());
                    programEsQueryDto.add(primeQueryDto);
                }
                
                //父节目类型id集合
                EsDataQueryDto parentProgramCategoryIdQueryDto = new EsDataQueryDto();
                parentProgramCategoryIdQueryDto.setParamName(ProgramDocumentParamName.PARENT_PROGRAM_CATEGORY_ID);
                parentProgramCategoryIdQueryDto.setParamValue(parentProgramCategoryId);
                programEsQueryDto.add(parentProgramCategoryIdQueryDto);
                //查询前7条
                PageInfo<ProgramListVo> pageInfo = businessEsHandle.queryPage(
                        SpringUtil.getPrefixDistinctionName() + "-" + ProgramDocumentParamName.INDEX_NAME,
                        ProgramDocumentParamName.INDEX_TYPE, programEsQueryDto, 1, 7, ProgramListVo.class);
                if (!pageInfo.getList().isEmpty()) {
                    ProgramHomeVo programHomeVo = new ProgramHomeVo();
                    programHomeVo.setCategoryName(pageInfo.getList().get(0).getParentProgramCategoryName());
                    programHomeVo.setCategoryId(pageInfo.getList().get(0).getParentProgramCategoryId());
                    programHomeVo.setProgramListVoList(pageInfo.getList());
                    programHomeVoList.add(programHomeVo);
                }
            }
        }catch (Exception e) {
            log.error("businessEsHandle.queryPage error",e);
        }
        return programHomeVoList;
    }
    
    public List<ProgramListVo> recommendList(ProgramRecommendListDto programRecommendListDto) {
        List<ProgramListVo> programListVoList = new ArrayList<>();
        try {
            boolean allQueryFlag = true;
            MatchAllQueryBuilder matchAllQueryBuilder = QueryBuilders.matchAllQuery();
            BoolQueryBuilder boolQuery = QueryBuilders.boolQuery();
            if (Objects.nonNull(programRecommendListDto.getAreaId())) {
                allQueryFlag = false;
                QueryBuilder builds = QueryBuilders.termQuery(ProgramDocumentParamName.AREA_ID, 
                        programRecommendListDto.getAreaId());
                boolQuery.must(builds);
            }
            if (Objects.nonNull(programRecommendListDto.getParentProgramCategoryId())) {
                allQueryFlag = false;
                QueryBuilder builds = QueryBuilders.termQuery(ProgramDocumentParamName.PARENT_PROGRAM_CATEGORY_ID, 
                        programRecommendListDto.getParentProgramCategoryId());
                boolQuery.must(builds);
            }
            if (Objects.nonNull(programRecommendListDto.getProgramId())) {
                allQueryFlag = false;
                QueryBuilder builds = QueryBuilders.termQuery(ProgramDocumentParamName.ID,
                        programRecommendListDto.getProgramId());
                boolQuery.mustNot(builds);
            }
            SearchSourceBuilder searchSourceBuilder = new SearchSourceBuilder();
            searchSourceBuilder.query(allQueryFlag ? matchAllQueryBuilder : boolQuery);
            searchSourceBuilder.trackTotalHits(true);
            searchSourceBuilder.from(1);
            searchSourceBuilder.size(10);
           
            Script script = new Script("Math.random()");
            ScriptSortBuilder scriptSortBuilder = new ScriptSortBuilder(script, ScriptSortBuilder.ScriptSortType.NUMBER);
            scriptSortBuilder.order(SortOrder.ASC);
            
            searchSourceBuilder.sort(scriptSortBuilder);
            
            businessEsHandle.executeQuery(
                    SpringUtil.getPrefixDistinctionName() + "-" + ProgramDocumentParamName.INDEX_NAME,
                    ProgramDocumentParamName.INDEX_TYPE,programListVoList,null,ProgramListVo.class,
                    searchSourceBuilder,null);
        }catch (Exception e) {
            log.error("recommendList error",e);
        }
        return programListVoList;
    }
    
    
    public PageVo<ProgramListVo> selectPage(ProgramPageListDto programPageListDto) {
        PageVo<ProgramListVo> pageVo = new PageVo<>();
        try {
            List<EsDataQueryDto> esDataQueryDtoList = new ArrayList<>();
            if (Objects.nonNull(programPageListDto.getAreaId())) {
                //地区id条件
                EsDataQueryDto areaIdQueryDto = new EsDataQueryDto();
                areaIdQueryDto.setParamName(ProgramDocumentParamName.AREA_ID);
                areaIdQueryDto.setParamValue(programPageListDto.getAreaId());
                esDataQueryDtoList.add(areaIdQueryDto);
            }else {
                //如果查全部地区，那么需要指定同一个节目分组内的主要节目
                EsDataQueryDto primeQueryDto = new EsDataQueryDto();
                primeQueryDto.setParamName(ProgramDocumentParamName.PRIME);
                primeQueryDto.setParamValue(BusinessStatus.YES.getCode());
                esDataQueryDtoList.add(primeQueryDto);
            }
            //父节目类型条件
            if (Objects.nonNull(programPageListDto.getParentProgramCategoryId())) {
                EsDataQueryDto parentProgramCategoryIdQueryDto = new EsDataQueryDto();
                parentProgramCategoryIdQueryDto.setParamName(ProgramDocumentParamName.PARENT_PROGRAM_CATEGORY_ID);
                parentProgramCategoryIdQueryDto.setParamValue(programPageListDto.getParentProgramCategoryId());
                esDataQueryDtoList.add(parentProgramCategoryIdQueryDto);
            }
            //节目类型条件
            if (Objects.nonNull(programPageListDto.getProgramCategoryId())) {
                EsDataQueryDto programCategoryIdQueryDto = new EsDataQueryDto();
                programCategoryIdQueryDto.setParamName(ProgramDocumentParamName.PROGRAM_CATEGORY_ID);
                programCategoryIdQueryDto.setParamValue(programPageListDto.getProgramCategoryId());
                esDataQueryDtoList.add(programCategoryIdQueryDto);
            }
            //开始日期和结束日期条件
            if (Objects.nonNull(programPageListDto.getStartDateTime()) &&
                    Objects.nonNull(programPageListDto.getEndDateTime())) {
                EsDataQueryDto showDayTimeQueryDto = new EsDataQueryDto();
                showDayTimeQueryDto.setParamName(ProgramDocumentParamName.SHOW_DAY_TIME);
                showDayTimeQueryDto.setStartTime(programPageListDto.getStartDateTime());
                showDayTimeQueryDto.setEndTime(programPageListDto.getEndDateTime());
                esDataQueryDtoList.add(showDayTimeQueryDto);
            }
            //构建排序信息
            ProgramPageOrder programPageOrder = getProgramPageOrder(programPageListDto);
            
            PageInfo<ProgramListVo> programListVoPageInfo = businessEsHandle.queryPage(
                    SpringUtil.getPrefixDistinctionName() + "-" + ProgramDocumentParamName.INDEX_NAME,
                    ProgramDocumentParamName.INDEX_TYPE, esDataQueryDtoList, programPageOrder.sortParam, 
                    programPageOrder.sortOrder, programPageListDto.getPageNumber(), programPageListDto.getPageSize(), 
                    ProgramListVo.class);
            pageVo = PageUtil.convertPage(programListVoPageInfo, programListVo -> programListVo);
        }catch (Exception e) {
            log.error("selectPage error",e);
        }
        return pageVo;
    }
    
    public ProgramPageOrder getProgramPageOrder(ProgramPageListDto programPageListDto){
        ProgramPageOrder programPageOrder = new ProgramPageOrder();
        switch (programPageListDto.getType()) {
            //推荐排序
            case 2:
                programPageOrder.sortParam = ProgramDocumentParamName.HIGH_HEAT;
                programPageOrder.sortOrder = SortOrder.DESC;
                break;
            //最近开场    
            case 3:
                programPageOrder.sortParam = ProgramDocumentParamName.SHOW_TIME;
                programPageOrder.sortOrder = SortOrder.ASC;
                break;
            //最新上架    
            case 4:
                programPageOrder.sortParam = ProgramDocumentParamName.ISSUE_TIME;
                programPageOrder.sortOrder = SortOrder.ASC;
                break;
            //相关度排序    
            default:
                programPageOrder.sortParam = null;
                programPageOrder.sortOrder = null;
        }
        return programPageOrder;
    }

    /**
     * 基于Elasticsearch的节目搜索实现
     * 多条件搜索：支持多种筛选条件的组合查询
     * 全文检索：支持标题和演员的模糊搜索
     * 分页查询：支持分页和排序
     * 高亮显示：搜索关键词高亮
     * 结果转换：将ES文档转换为业务VO
     * 异常处理：捕获并记录异常
     * @param programSearchDto
     * @return
     */
    public PageVo<ProgramListVo> search(ProgramSearchDto programSearchDto) {
        PageVo<ProgramListVo> pageVo = new PageVo<>();
        try {
            //创建最外层的 BoolQueryBuilder
            BoolQueryBuilder boolQuery = QueryBuilders.boolQuery();
            //areaId条件查询
            if (Objects.nonNull(programSearchDto.getAreaId())) {
                QueryBuilder builds = QueryBuilders.termQuery(ProgramDocumentParamName.AREA_ID, programSearchDto.getAreaId());
                boolQuery.must(builds);
            }
            //父节目类型id条件查询
            if (Objects.nonNull(programSearchDto.getParentProgramCategoryId())) {
                QueryBuilder builds = QueryBuilders.termQuery(ProgramDocumentParamName.PARENT_PROGRAM_CATEGORY_ID, programSearchDto.getParentProgramCategoryId());
                boolQuery.must(builds);
            }
            //时间范围条件查询
            if (Objects.nonNull(programSearchDto.getStartDateTime()) &&
                    Objects.nonNull(programSearchDto.getEndDateTime())) {
                QueryBuilder builds = QueryBuilders.rangeQuery(ProgramDocumentParamName.SHOW_DAY_TIME)
                        .from(programSearchDto.getStartDateTime()).to(programSearchDto.getEndDateTime()).includeLower(true);
                boolQuery.must(builds);
            }
            //输入内容条件查询
            if (StringUtil.isNotEmpty(programSearchDto.getContent())) {
                // 创建内层的 BoolQueryBuilder 用于处理 title 或 actor 的 OR 查询
                BoolQueryBuilder innerBoolQuery = QueryBuilders.boolQuery();
                //按节目标题搜索
                innerBoolQuery.should(QueryBuilders.matchQuery(ProgramDocumentParamName.TITLE, programSearchDto.getContent()));
                //按艺人名字搜索
                innerBoolQuery.should(QueryBuilders.matchQuery(ProgramDocumentParamName.ACTOR, programSearchDto.getContent()));
                // 确保至少有一个 should 条件匹配
                innerBoolQuery.minimumShouldMatch(1);
                // 将内层的 BoolQueryBuilder 添加到最外层的查询中
                boolQuery.must(innerBoolQuery);
            }

            //使用 SearchSourceBuilder 构建最终的查询
            SearchSourceBuilder searchSourceBuilder = new SearchSourceBuilder();
            //构建排序信息
            ProgramPageOrder programPageOrder = getProgramPageOrder(programSearchDto);
            if (Objects.nonNull(programPageOrder.sortParam) && Objects.nonNull(programPageOrder.sortOrder)) {
                FieldSortBuilder sort = SortBuilders.fieldSort(programPageOrder.sortParam);
                sort.order(programPageOrder.sortOrder);
                searchSourceBuilder.sort(sort);
            }
            searchSourceBuilder.query(boolQuery);
            //设置是否计算总命中数
            searchSourceBuilder.trackTotalHits(true);
            //页码
            searchSourceBuilder.from((programSearchDto.getPageNumber() - 1) * programSearchDto.getPageSize());
            //页大小
            searchSourceBuilder.size(programSearchDto.getPageSize());
            //设置高亮显示字段
            searchSourceBuilder.highlighter(getHighlightBuilder(Arrays.asList(ProgramDocumentParamName.TITLE,
                    ProgramDocumentParamName.ACTOR)));
            //构建分页对象
            List<ProgramListVo> list = new ArrayList<>();
            PageInfo<ProgramListVo> pageInfo = new PageInfo<>(list);
            pageInfo.setPageNum(programSearchDto.getPageNumber());
            pageInfo.setPageSize(programSearchDto.getPageSize());
            //执行查询
            businessEsHandle.executeQuery(SpringUtil.getPrefixDistinctionName() + "-" + ProgramDocumentParamName.INDEX_NAME,
                    ProgramDocumentParamName.INDEX_TYPE,list,pageInfo,ProgramListVo.class,
                    searchSourceBuilder,Arrays.asList(ProgramDocumentParamName.TITLE,ProgramDocumentParamName.ACTOR));
            pageVo = PageUtil.convertPage(pageInfo,programListVo -> programListVo);
        }catch (Exception e) {
            log.error("search error",e);
        }
        return pageVo;
    }
    
    public HighlightBuilder getHighlightBuilder(List<String> fieldNameList){
        // 创建一个HighlightBuilder
        HighlightBuilder highlightBuilder = new HighlightBuilder();
        for (String fieldName : fieldNameList) {
            // 为特定字段添加高亮设置
            HighlightBuilder.Field highlightTitle = new HighlightBuilder.Field(fieldName);
            highlightTitle.preTags("<em>");
            highlightTitle.postTags("</em>");
            highlightBuilder.field(highlightTitle);
        }
        return highlightBuilder;
    }
    
    public void deleteByProgramId(Long programId){
        try {
            List<EsDataQueryDto> esDataQueryDtoList = new ArrayList<>();
            EsDataQueryDto programIdDto = new EsDataQueryDto();
            programIdDto.setParamName(ProgramDocumentParamName.ID);
            programIdDto.setParamValue(programId);
            esDataQueryDtoList.add(programIdDto);
            
            List<ProgramListVo> programListVos = 
                    businessEsHandle.query(
                            SpringUtil.getPrefixDistinctionName() + "-" + ProgramDocumentParamName.INDEX_NAME, 
                            ProgramDocumentParamName.INDEX_TYPE, 
                            esDataQueryDtoList, 
                            ProgramListVo.class);
            if (CollectionUtil.isNotEmpty(programListVos)) {
                for (ProgramListVo programListVo : programListVos) {
                    businessEsHandle.deleteByDocumentId(
                            SpringUtil.getPrefixDistinctionName() + "-" + ProgramDocumentParamName.INDEX_NAME,
                            programListVo.getEsId());
                }
            }
        }catch (Exception e) {
            log.error("deleteByProgramId error",e);
        }
    }
}
