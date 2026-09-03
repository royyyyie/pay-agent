package com.damai.service;

import cn.hutool.core.bean.BeanUtil;
import cn.hutool.core.collection.CollectionUtil;
import com.alibaba.fastjson.JSON;
import com.baidu.fsg.uid.UidGenerator;
import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.core.conditions.update.LambdaUpdateWrapper;
import com.baomidou.mybatisplus.core.metadata.IPage;
import com.baomidou.mybatisplus.core.toolkit.Wrappers;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.damai.BusinessThreadPool;
import com.damai.RedisStreamPushHandler;
import com.damai.client.BaseDataClient;
import com.damai.client.OrderClient;
import com.damai.client.UserClient;
import com.damai.common.ApiResponse;
import com.damai.core.RedisKeyManage;
import com.damai.dto.AccountOrderCountDto;
import com.damai.dto.AreaGetDto;
import com.damai.dto.AreaSelectDto;
import com.damai.dto.ProgramAddDto;
import com.damai.dto.ProgramGetDto;
import com.damai.dto.ProgramInvalidDto;
import com.damai.dto.ProgramListDto;
import com.damai.dto.ProgramOperateDataDto;
import com.damai.dto.ProgramPageListDto;
import com.damai.dto.ProgramRecommendListDto;
import com.damai.dto.ProgramResetExecuteDto;
import com.damai.dto.ProgramSearchDto;
import com.damai.dto.TicketCategoryCountDto;
import com.damai.dto.TicketUserListDto;
import com.damai.entity.Program;
import com.damai.entity.ProgramCategory;
import com.damai.entity.ProgramGroup;
import com.damai.entity.ProgramJoinShowTime;
import com.damai.entity.ProgramShowTime;
import com.damai.entity.Seat;
import com.damai.entity.TicketCategory;
import com.damai.entity.TicketCategoryAggregate;
import com.damai.enums.BaseCode;
import com.damai.enums.BusinessStatus;
import com.damai.enums.CompositeCheckType;
import com.damai.enums.SellStatus;
import com.damai.exception.DaMaiFrameException;
import com.damai.initialize.impl.composite.CompositeContainer;
import com.damai.mapper.ProgramCategoryMapper;
import com.damai.mapper.ProgramGroupMapper;
import com.damai.mapper.ProgramMapper;
import com.damai.mapper.ProgramShowTimeMapper;
import com.damai.mapper.SeatMapper;
import com.damai.mapper.TicketCategoryMapper;
import com.damai.page.PageUtil;
import com.damai.page.PageVo;
import com.damai.redis.RedisCache;
import com.damai.redis.RedisKeyBuild;
import com.damai.repeatexecutelimit.annotion.RepeatExecuteLimit;
import com.damai.service.cache.local.LocalCacheProgram;
import com.damai.service.cache.local.LocalCacheProgramCategory;
import com.damai.service.cache.local.LocalCacheProgramGroup;
import com.damai.service.cache.local.LocalCacheProgramShowTime;
import com.damai.service.cache.local.LocalCacheTicketCategory;
import com.damai.service.constant.ProgramTimeType;
import com.damai.service.es.ProgramEs;
import com.damai.service.lua.ProgramDelCacheData;
import com.damai.service.tool.TokenExpireManager;
import com.damai.servicelock.LockType;
import com.damai.servicelock.annotion.ServiceLock;
import com.damai.threadlocal.BaseParameterHolder;
import com.damai.util.DateUtils;
import com.damai.util.ServiceLockTool;
import com.damai.util.StringUtil;
import com.damai.vo.AccountOrderCountVo;
import com.damai.vo.AreaVo;
import com.damai.vo.ProgramGroupVo;
import com.damai.vo.ProgramHomeVo;
import com.damai.vo.ProgramListVo;
import com.damai.vo.ProgramSimpleInfoVo;
import com.damai.vo.ProgramVo;
import com.damai.vo.TicketCategoryVo;
import com.damai.vo.TicketUserVo;
import lombok.extern.slf4j.Slf4j;
import org.redisson.api.RLock;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.context.annotation.Lazy;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.ArrayList;
import java.util.Collection;
import java.util.Date;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Map.Entry;
import java.util.Objects;
import java.util.Optional;
import java.util.Set;
import java.util.concurrent.TimeUnit;
import java.util.stream.Collectors;

import static com.damai.constant.Constant.CODE;
import static com.damai.constant.Constant.USER_ID;
import static com.damai.core.DistributedLockConstants.GET_PROGRAM_LOCK;
import static com.damai.core.DistributedLockConstants.PROGRAM_GROUP_LOCK;
import static com.damai.core.DistributedLockConstants.PROGRAM_LOCK;
import static com.damai.core.RepeatExecuteLimitConstants.CANCEL_PROGRAM_ORDER;
import static com.damai.util.DateUtils.FORMAT_DATE;

/**
 * @program: 极度真实还原大麦网高并发实战项目。 添加 阿星不是程序员 微信，添加时备注 大麦 来获取项目的完整资料 
 * @description: 节目 service
 * @author: 阿星不是程序员
 **/
@Slf4j
@Service
public class ProgramService extends ServiceImpl<ProgramMapper, Program> {
    
    @Autowired
    private UidGenerator uidGenerator;
    
    @Autowired
    private ProgramMapper programMapper;
    
    @Autowired
    private ProgramGroupMapper programGroupMapper;
    
    @Autowired
    private ProgramShowTimeMapper programShowTimeMapper;
    
    @Autowired
    private ProgramCategoryMapper programCategoryMapper; 
    
    @Autowired
    private TicketCategoryMapper ticketCategoryMapper;
    
    @Autowired
    private SeatMapper seatMapper;
    
    @Autowired
    private BaseDataClient baseDataClient;
    
    @Autowired
    private UserClient userClient;
    
    @Autowired
    private OrderClient orderClient;
    
    @Autowired
    private RedisCache redisCache;
    
    @Lazy
    @Autowired
    private ProgramService programService;
    
    @Autowired
    private ProgramShowTimeService programShowTimeService;
    
    @Autowired
    private TicketCategoryService ticketCategoryService;
    
    @Autowired
    private ProgramCategoryService programCategoryService;
    
    @Autowired
    private ProgramEs programEs;
    
    @Autowired
    private ServiceLockTool serviceLockTool;
    
    @Autowired
    private RedisStreamPushHandler redisStreamPushHandler;
    
    @Autowired
    private LocalCacheProgram localCacheProgram;
    
    @Autowired
    private LocalCacheProgramGroup localCacheProgramGroup;
    
    @Autowired
    private LocalCacheProgramCategory localCacheProgramCategory;
    
    @Autowired
    private LocalCacheProgramShowTime localCacheProgramShowTime;
    
    @Autowired
    private LocalCacheTicketCategory localCacheTicketCategory;
    
    @Autowired
    private CompositeContainer compositeContainer;
    
    @Autowired
    private TokenExpireManager tokenExpireManager;
    
    @Autowired
    private ProgramDelCacheData programDelCacheData;
    
    /**
     * 添加节目
     * @param programAddDto 添加节目数据的入参
     * @return 添加节目后的id
     * */
    public Long add(ProgramAddDto programAddDto){
        Program program = new Program();
        BeanUtil.copyProperties(programAddDto,program);
        program.setId(uidGenerator.getUid());
        programMapper.insert(program);
        return program.getId();
    }
    
    /**
     * 搜索的功能
     * 关于 Elasticsearch 的详细讲解，可到超级八股文中进行学习：<a href="https://javaup.chat/">...</a>
     * @param programSearchDto 搜索节目数据的入参
     * @return 执行后的结果
     * */
    public PageVo<ProgramListVo> search(ProgramSearchDto programSearchDto) {
        //将入参的参数进行具体的组装
        setQueryTime(programSearchDto);
        //使用elasticsearch查询
        return programEs.search(programSearchDto);
    }
    
    /**
     * 查询主页信息
     * @param programListDto 查询节目数据的入参
     * @return 执行后的结果
     * */
    public List<ProgramHomeVo> selectHomeList(ProgramListDto programListDto) {
        //先从elasticsearch中查询
        List<ProgramHomeVo> programHomeVoList = programEs.selectHomeList(programListDto);
        if (CollectionUtil.isNotEmpty(programHomeVoList)) {
            return programHomeVoList;
        }
        //elasticsearch中查询不到，再去数据库中查询
        return dbSelectHomeList(programListDto);
    }
    
    /**
     * 查询主页信息（数据库查询）
     * @param programPageListDto 查询节目数据的入参
     * @return 执行后的结果
     * */
    private List<ProgramHomeVo> dbSelectHomeList(ProgramListDto programPageListDto){
        List<ProgramHomeVo> programHomeVoList = new ArrayList<>();
        //根据父节目类型id来查询节目类型map，key：节目类型id，value：节目类型名
        Map<Long, String> programCategoryMap = selectProgramCategoryMap(programPageListDto.getParentProgramCategoryIds());
        //查询节目列表
        List<Program> programList = programMapper.selectHomeList(programPageListDto);
        if (CollectionUtil.isEmpty(programList)) {
            return programHomeVoList;
        }
        //节目id集合
        List<Long> programIdList = programList.stream().map(Program::getId).collect(Collectors.toList());
        //查询出所有的节目相对应的演出时间
        LambdaQueryWrapper<ProgramShowTime> programShowTimeLambdaQueryWrapper = Wrappers.lambdaQuery(ProgramShowTime.class)
                .in(ProgramShowTime::getProgramId, programIdList);
        List<ProgramShowTime> programShowTimeList = programShowTimeMapper.selectList(programShowTimeLambdaQueryWrapper);
        //key:节目id；value:节目演出时间列表
        Map<Long, List<ProgramShowTime>> programShowTimeMap =
                programShowTimeList.stream().collect(Collectors.groupingBy(ProgramShowTime::getProgramId));
        //找出节目相对应的最低最高价
        Map<Long, TicketCategoryAggregate> ticketCategorieMap = selectTicketCategorieMap(programIdList);
        //将节目结合按照父节目类型id进行分组成map，key：父节目类型id，value：节目集合
        Map<Long, List<Program>> programMap = programList.stream()
                .collect(Collectors.groupingBy(Program::getParentProgramCategoryId));
        //遍历所有的父分类
        for (Entry<Long, List<Program>> programEntry : programMap.entrySet()) {
            //父节目类型id
            Long key = programEntry.getKey();
            //节目集合
            List<Program> value = programEntry.getValue();
            List<ProgramListVo> programListVoList = new ArrayList<>();
            //循环节目集合
            for (Program program : value) {
                ProgramListVo programListVo = new ProgramListVo();
                BeanUtil.copyProperties(program,programListVo);
                
                programListVo.setShowTime(Optional.ofNullable(programShowTimeMap.get(program.getId()))
                        .filter(list -> !list.isEmpty())
                        .map(list -> list.get(0))
                        .map(ProgramShowTime::getShowTime)
                        .orElse(null));
                programListVo.setShowDayTime(Optional.ofNullable(programShowTimeMap.get(program.getId()))
                        .filter(list -> !list.isEmpty())
                        .map(list -> list.get(0))
                        .map(ProgramShowTime::getShowDayTime)
                        .orElse(null));
                programListVo.setShowWeekTime(Optional.ofNullable(programShowTimeMap.get(program.getId()))
                        .filter(list -> !list.isEmpty())
                        .map(list -> list.get(0))
                        .map(ProgramShowTime::getShowWeekTime)
                        .orElse(null));
                
                programListVo.setMaxPrice(Optional.ofNullable(ticketCategorieMap.get(program.getId()))
                        .map(TicketCategoryAggregate::getMaxPrice).orElse(null));
                programListVo.setMinPrice(Optional.ofNullable(ticketCategorieMap.get(program.getId()))
                        .map(TicketCategoryAggregate::getMinPrice).orElse(null));
                programListVoList.add(programListVo);
            }
            ProgramHomeVo programHomeVo = new ProgramHomeVo();
            programHomeVo.setCategoryName(programCategoryMap.get(key));
            programHomeVo.setCategoryId(key);
            programHomeVo.setProgramListVoList(programListVoList);
            programHomeVoList.add(programHomeVo);
        }
        return programHomeVoList;
    }
    
    /**
     * 组装节目参数
     * @param programPageListDto 节目数据的入参
     * */
    public void setQueryTime(ProgramPageListDto programPageListDto){
        switch (programPageListDto.getTimeType()) {
            case ProgramTimeType.TODAY:
                programPageListDto.setStartDateTime(DateUtils.now(FORMAT_DATE));
                programPageListDto.setEndDateTime(DateUtils.now(FORMAT_DATE));
                break;
            case ProgramTimeType.TOMORROW:
                programPageListDto.setStartDateTime(DateUtils.now(FORMAT_DATE));
                programPageListDto.setEndDateTime(DateUtils.addDay(DateUtils.now(FORMAT_DATE),1));
                break;
            case ProgramTimeType.WEEK:
                programPageListDto.setStartDateTime(DateUtils.now(FORMAT_DATE));
                programPageListDto.setEndDateTime(DateUtils.addWeek(DateUtils.now(FORMAT_DATE),1));
                break;
            case ProgramTimeType.MONTH:
                programPageListDto.setStartDateTime(DateUtils.now(FORMAT_DATE));
                programPageListDto.setEndDateTime(DateUtils.addMonth(DateUtils.now(FORMAT_DATE),1));
                break;
            case ProgramTimeType.CALENDAR:
                if (Objects.isNull(programPageListDto.getStartDateTime())) {
                    throw new DaMaiFrameException(BaseCode.START_DATE_TIME_NOT_EXIST);
                }
                if (Objects.isNull(programPageListDto.getEndDateTime())) {
                    throw new DaMaiFrameException(BaseCode.END_DATE_TIME_NOT_EXIST);
                }
                break;
            default:
                programPageListDto.setStartDateTime(null);
                programPageListDto.setEndDateTime(null);
                break;
        }
    }
    
    /**
     * 查询分类列表（数据库查询）
     * @param programPageListDto 查询节目数据的入参
     * @return 执行后的结果
     * */
    public PageVo<ProgramListVo> selectPage(ProgramPageListDto programPageListDto) {
        //处理时间范围参数
        setQueryTime(programPageListDto);
        //使用elasticsearch查询
        PageVo<ProgramListVo> pageVo = programEs.selectPage(programPageListDto);
        //elasticsearch查询不到，在数据库中查询
        if (CollectionUtil.isNotEmpty(pageVo.getList())) {
            return pageVo;
        }
        return dbSelectPage(programPageListDto);
    }
    
    /**
     * 推荐列表
     * @param programRecommendListDto 查询节目数据的入参
     * @return 执行后的结果
     * */
    public List<ProgramListVo> recommendList(ProgramRecommendListDto programRecommendListDto){
        compositeContainer.execute(CompositeCheckType.PROGRAM_RECOMMEND_CHECK.getValue(),programRecommendListDto);
        return programEs.recommendList(programRecommendListDto);
    }
    
    /**
     * 数据库分页查询​：查询节目和演出时间关联数据
     *
     * ​批量数据获取​：批量获取分类、票价、地区等关联信息
     *
     * ​数据转换​：将数据库实体转换为前端需要的VO对象
     *
     * ​微服务调用​：调用基础数据服务获取地区信息
     * 查询分类信息（数据库查询）
     * @param programPageListDto 查询节目数据的入参
     * @return 执行后的结果
     * */
    public PageVo<ProgramListVo> dbSelectPage(ProgramPageListDto programPageListDto) {
        //需要program和program_show_time连表查询
        IPage<ProgramJoinShowTime> iPage =
                programMapper.selectPage(PageUtil.getPageParams(programPageListDto), programPageListDto);
        //如果查询的节目列表为空，则直接返回pageVo对象
        if (CollectionUtil.isEmpty(iPage.getRecords())) {
            return new PageVo<>(iPage.getCurrent(), iPage.getSize(), iPage.getTotal(), new ArrayList<>());
        }
        //提取所有节目的分类ID
        Set<Long> programCategoryIdList = 
                iPage.getRecords().stream().map(Program::getProgramCategoryId).collect(Collectors.toSet());
        // 批量查询分类信息
        Map<Long, String> programCategoryMap = selectProgramCategoryMap(programCategoryIdList);
        // 提取所有节目ID
        List<Long> programIdList = iPage.getRecords().stream().map(Program::getId).collect(Collectors.toList());
        // 批量查询票价信息
        Map<Long, TicketCategoryAggregate> ticketCategorieMap = selectTicketCategorieMap(programIdList);
        // 提取去重后的地区ID
        Map<Long,String> tempAreaMap = new HashMap<>(64);
        AreaSelectDto areaSelectDto = new AreaSelectDto();
        areaSelectDto.setIdList(iPage.getRecords().stream().map(Program::getAreaId).distinct().collect(Collectors.toList()));
        ApiResponse<List<AreaVo>> areaResponse = baseDataClient.selectByIdList(areaSelectDto);
        if (Objects.equals(areaResponse.getCode(), ApiResponse.ok().getCode())) {
            if (CollectionUtil.isNotEmpty(areaResponse.getData())) {
                tempAreaMap = areaResponse.getData().stream()
                        .collect(Collectors.toMap(AreaVo::getId,AreaVo::getName,(v1,v2) -> v2));
            }
        }else {
            log.error("base-data selectByIdList rpc error areaResponse:{}", JSON.toJSONString(areaResponse));
        }
        Map<Long,String> areaMap = tempAreaMap;
        
        return PageUtil.convertPage(iPage, programJoinShowTime -> {
            // 对每个记录进行转换
            ProgramListVo programListVo = new ProgramListVo();
            BeanUtil.copyProperties(programJoinShowTime, programListVo);
            // 设置地区名称
            programListVo.setAreaName(areaMap.get(programJoinShowTime.getAreaId()));
            // 设置分类名称
            programListVo.setProgramCategoryName(programCategoryMap.get(programJoinShowTime.getProgramCategoryId()));
            // 设置最低价
            programListVo.setMinPrice(Optional.ofNullable(ticketCategorieMap.get(programJoinShowTime.getId()))
                    .map(TicketCategoryAggregate::getMinPrice).orElse(null));
            // 设置最高价
            programListVo.setMaxPrice(Optional.ofNullable(ticketCategorieMap.get(programJoinShowTime.getId()))
                    .map(TicketCategoryAggregate::getMaxPrice).orElse(null));
            return programListVo;
        });
    }
    
    /**
     * 查询节目详情
     * @param programGetDto 查询节目数据的入参
     * @return 执行后的结果
     * */
    public ProgramVo detail(ProgramGetDto programGetDto) {
        compositeContainer.execute(CompositeCheckType.PROGRAM_DETAIL_CHECK.getValue(),programGetDto);
        return getDetail(programGetDto);
    }
    
    /**
     * 查询节目详情V1
     * @param programGetDto 查询节目数据的入参
     * @return 执行后的结果
     * */
    public ProgramVo detailV1(ProgramGetDto programGetDto) {
        compositeContainer.execute(CompositeCheckType.PROGRAM_DETAIL_CHECK.getValue(),programGetDto);
        return getDetail(programGetDto);
    }
    
    /**
     * 查询节目详情V2
     * @param programGetDto 查询节目数据的入参
     * @return 执行后的结果
     * */
    public ProgramVo detailV2(ProgramGetDto programGetDto) {
        compositeContainer.execute(CompositeCheckType.PROGRAM_DETAIL_CHECK.getValue(),programGetDto);
        return getDetailV2(programGetDto);
    }
    
    /**
     * 查询节目详情执行
     * @param programGetDto 查询节目数据的入参
     * @return 执行后的结果
     * */
    public ProgramVo getDetail(ProgramGetDto programGetDto) {
        //获取演出时间  都是先从缓存中查取，如果没有则拆线呢数据库
        ProgramShowTime programShowTime = programShowTimeService.selectProgramShowTimeByProgramId(programGetDto.getId());
        //获取节目信息
        ProgramVo programVo = programService.getById(programGetDto.getId(),DateUtils.countBetweenSecond(DateUtils.now(),
                programShowTime.getShowTime()), TimeUnit.SECONDS);
        programVo.setShowTime(programShowTime.getShowTime());
        programVo.setShowDayTime(programShowTime.getShowDayTime());
        programVo.setShowWeekTime(programShowTime.getShowWeekTime());
        //获取节目相关组信息
        ProgramGroupVo programGroupVo = programService.getProgramGroup(programVo.getProgramGroupId());
        programVo.setProgramGroupVo(programGroupVo);
        //预加载购票人信息
        preloadTicketUserList(programVo.getHighHeat());
        // 预加载账户订单
        preloadAccountOrderCount(programVo.getId());
        // 加载节目分类信息
        ProgramCategory programCategory = getProgramCategory(programVo.getProgramCategoryId());
        if (Objects.nonNull(programCategory)) {
            programVo.setProgramCategoryName(programCategory.getName());
        }
        //父分类信息
        ProgramCategory parentProgramCategory = getProgramCategory(programVo.getParentProgramCategoryId());
        if (Objects.nonNull(parentProgramCategory)) {
            programVo.setParentProgramCategoryName(parentProgramCategory.getName());
        }
        
        List<TicketCategoryVo> ticketCategoryVoList =
                ticketCategoryService.selectTicketCategoryListByProgramId(programVo.getId(),
                        DateUtils.countBetweenSecond(DateUtils.now(),programShowTime.getShowTime()), TimeUnit.SECONDS);
        programVo.setTicketCategoryVoList(ticketCategoryVoList);
        
        return programVo;
    }
    
    /**
     * 查询节目详情V2执行  多级缓存的版本
     * @param programGetDto 查询节目数据的入参
     * @return 执行后的结果
     * */
    public ProgramVo getDetailV2(ProgramGetDto programGetDto) {
        //查询节目演出时间
        ProgramShowTime programShowTime =
                programShowTimeService.selectProgramShowTimeByProgramIdMultipleCache(programGetDto.getId());

        //从节目表获取数据，以及区域信息
        ProgramVo programVo = programService.getByIdMultipleCache(programGetDto.getId(),programShowTime.getShowTime());
        
        programVo.setShowTime(programShowTime.getShowTime());
        programVo.setShowDayTime(programShowTime.getShowDayTime());
        programVo.setShowWeekTime(programShowTime.getShowWeekTime());
        //从节目分组表获取数据
        ProgramGroupVo programGroupVo = programService.getProgramGroupMultipleCache(programVo.getProgramGroupId());
        programVo.setProgramGroupVo(programGroupVo);

        //预先加载用户购票人
        preloadTicketUserList(programVo.getHighHeat());

        //预先加载用户下节目订单数量
        preloadAccountOrderCount(programVo.getId());

        //设置节目类型相关信息
        ProgramCategory programCategory = getProgramCategoryMultipleCache(programVo.getProgramCategoryId());
        if (Objects.nonNull(programCategory)) {
            programVo.setProgramCategoryName(programCategory.getName());
        }
        ProgramCategory parentProgramCategory = getProgramCategoryMultipleCache(programVo.getParentProgramCategoryId());
        if (Objects.nonNull(parentProgramCategory)) {
            programVo.setParentProgramCategoryName(parentProgramCategory.getName());
        }

        //查询节目票档
        List<TicketCategoryVo> ticketCategoryVoList = ticketCategoryService
                .selectTicketCategoryListByProgramIdMultipleCache(programVo.getId(),programShowTime.getShowTime());
        programVo.setTicketCategoryVoList(ticketCategoryVoList);
        
        return programVo;
    }
    
    /**
     * 查询节目表详情执行（多级）
     * @param programId 节目id
     * @param showTime 节目演出时间
     * @return 执行后的结果
     * */
    public ProgramVo getByIdMultipleCache(Long programId, Date showTime){
        /**
         * 查询节目(结合多级缓存查询)
         * */
        return localCacheProgram.getCache(RedisKeyBuild.createRedisKey(RedisKeyManage.PROGRAM, programId).getRelKey(),
                key -> {
                    log.info("查询节目详情 从本地缓存没有查询到 节目id : {}",programId);
                    ProgramVo programVo = getById(programId,DateUtils.countBetweenSecond(DateUtils.now(),showTime),
                            TimeUnit.SECONDS);
                    programVo.setShowTime(showTime);
                    return programVo;
                });
    }
    
    public ProgramVo simpleGetByIdMultipleCache(Long programId){
        ProgramVo programVoCache = localCacheProgram.getCache(RedisKeyBuild.createRedisKey(RedisKeyManage.PROGRAM, 
                programId).getRelKey());
        if (Objects.nonNull(programVoCache)) {
            return programVoCache;
        }
        return redisCache.get(RedisKeyBuild.createRedisKey(RedisKeyManage.PROGRAM, programId), ProgramVo.class);
    }
    
    public ProgramVo simpleGetProgramAndShowMultipleCache(Long programId){
        ProgramShowTime programShowTime =
                programShowTimeService.simpleSelectProgramShowTimeByProgramIdMultipleCache(programId);
        if (Objects.isNull(programShowTime)) {
            throw new DaMaiFrameException(BaseCode.PROGRAM_SHOW_TIME_NOT_EXIST);
        }
        
        ProgramVo programVo = simpleGetByIdMultipleCache(programId);
        if (Objects.isNull(programVo)) {
            throw new DaMaiFrameException(BaseCode.PROGRAM_NOT_EXIST);
        }
        
        programVo.setShowTime(programShowTime.getShowTime());
        programVo.setShowDayTime(programShowTime.getShowDayTime());
        programVo.setShowWeekTime(programShowTime.getShowWeekTime());
        
        return programVo;
    }
    
    @ServiceLock(lockType= LockType.Read,name = PROGRAM_LOCK,keys = {"#programId"})
    public ProgramVo getById(Long programId,Long expireTime,TimeUnit timeUnit) {
        //先从缓存中查询
        ProgramVo programVo =
                redisCache.get(RedisKeyBuild.createRedisKey(RedisKeyManage.PROGRAM, programId), ProgramVo.class);
        //如果存在直接返回数据
        if (Objects.nonNull(programVo)) {
            return programVo;
        }
        //加锁
        log.info("查询节目详情 从Redis缓存没有查询到 节目id : {}",programId);
        RLock lock = serviceLockTool.getLock(LockType.Reentrant, GET_PROGRAM_LOCK, new String[]{String.valueOf(programId)});
        lock.lock();
        try {
            //再从缓存中查询，如果缓存不存在则从数据库中查询再放入到缓存中
            return redisCache.get(RedisKeyBuild.createRedisKey(RedisKeyManage.PROGRAM,programId)
                    ,ProgramVo.class,
                    () -> createProgramVo(programId)
                    //缓存的过期时间设置到节目的演出时间
                    ,expireTime,
                    timeUnit);
        }finally {
            //解锁
            lock.unlock();
        }
    }
    
    public ProgramGroupVo getProgramGroupMultipleCache(Long programGroupId){
        return localCacheProgramGroup.getCache(
                RedisKeyBuild.createRedisKey(RedisKeyManage.PROGRAM_GROUP, programGroupId).getRelKey(),
                key -> getProgramGroup(programGroupId));
    }
    @ServiceLock(lockType= LockType.Read,name = PROGRAM_GROUP_LOCK,keys = {"#programGroupId"})
    public ProgramGroupVo getProgramGroup(Long programGroupId) {
        ProgramGroupVo programGroupVo =
                redisCache.get(RedisKeyBuild.createRedisKey(RedisKeyManage.PROGRAM_GROUP, programGroupId), ProgramGroupVo.class);
        if (Objects.nonNull(programGroupVo)) {
            return programGroupVo;
        }
        RLock lock = serviceLockTool.getLock(LockType.Reentrant, GET_PROGRAM_LOCK, new String[]{String.valueOf(programGroupId)});
        lock.lock();
        try {
            programGroupVo = redisCache.get(RedisKeyBuild.createRedisKey(RedisKeyManage.PROGRAM_GROUP, programGroupId), 
                    ProgramGroupVo.class);
            if (Objects.isNull(programGroupVo)) {
                programGroupVo = createProgramGroupVo(programGroupId);
                redisCache.set(RedisKeyBuild.createRedisKey(RedisKeyManage.PROGRAM_GROUP, programGroupId),programGroupVo,
                        DateUtils.countBetweenSecond(DateUtils.now(),programGroupVo.getRecentShowTime()),TimeUnit.SECONDS);
            }
            return programGroupVo;
        }finally {
            lock.unlock();
        }
    }
    
    public Map<Long, String> selectProgramCategoryMap(Collection<Long> programCategoryIdList){
        LambdaQueryWrapper<ProgramCategory> pcLambdaQueryWrapper = Wrappers.lambdaQuery(ProgramCategory.class)
                .in(ProgramCategory::getId, programCategoryIdList);
        List<ProgramCategory> programCategoryList = programCategoryMapper.selectList(pcLambdaQueryWrapper);
        return programCategoryList
                .stream()
                .collect(Collectors.toMap(ProgramCategory::getId, ProgramCategory::getName, (v1, v2) -> v2));
    }

    /**
     * 遍历找出每个节目的最低最高价
     * @param programIdList
     * @return
     */
    public Map<Long, TicketCategoryAggregate> selectTicketCategorieMap(List<Long> programIdList){
        List<TicketCategoryAggregate> ticketCategorieList = ticketCategoryMapper.selectAggregateList(programIdList);
        return ticketCategorieList
                .stream()
                .collect(Collectors.toMap(TicketCategoryAggregate::getProgramId, 
                        ticketCategory -> ticketCategory, (v1, v2) -> v2));
    }
    
    @RepeatExecuteLimit(name = CANCEL_PROGRAM_ORDER,keys = {"#programOperateDataDto.programId","#programOperateDataDto.seatIdList"})
    @Transactional(rollbackFor = Exception.class)
    public void operateProgramData(ProgramOperateDataDto programOperateDataDto){
        List<TicketCategoryCountDto> ticketCategoryCountDtoList = programOperateDataDto.getTicketCategoryCountDtoList();
        //从库中查询座位集合
        List<Long> seatIdList = programOperateDataDto.getSeatIdList();
        LambdaQueryWrapper<Seat> seatLambdaQueryWrapper = 
                Wrappers.lambdaQuery(Seat.class)
                        .eq(Seat::getProgramId,programOperateDataDto.getProgramId())
                        .in(Seat::getId, seatIdList);
        List<Seat> seatList = seatMapper.selectList(seatLambdaQueryWrapper);
        //如果库中的座位集合为空，则抛出异常
        if (CollectionUtil.isEmpty(seatList)) {
            throw new DaMaiFrameException(BaseCode.SEAT_NOT_EXIST);
        }
        //如果库中的座位集合数量和传入的座位数量不相同，则抛出异常
        if (seatList.size() != seatIdList.size()) {
            throw new DaMaiFrameException(BaseCode.SEAT_UPDATE_REL_COUNT_NOT_EQUAL_PRESET_COUNT);
        }
        for (Seat seat : seatList) {
            //如果库中的座位有一个已经是已售卖的状态，则抛出异常
            if (Objects.equals(seat.getSellStatus(), SellStatus.SOLD.getCode())) {
                throw new DaMaiFrameException(BaseCode.SEAT_SOLD);
            }
        }
        //将库中的座位集合批量更新为售卖状态
        LambdaUpdateWrapper<Seat> seatLambdaUpdateWrapper =
                Wrappers.lambdaUpdate(Seat.class)
                        .eq(Seat::getProgramId,programOperateDataDto.getProgramId())
                        .in(Seat::getId, seatIdList);
        Seat updateSeat = new Seat();
        updateSeat.setSellStatus(SellStatus.SOLD.getCode());
        seatMapper.update(updateSeat,seatLambdaUpdateWrapper);

        //将库中的对应票档进行更新库存
        int updateRemainNumberCount =
                ticketCategoryMapper.batchUpdateRemainNumber(ticketCategoryCountDtoList,programOperateDataDto.getProgramId());
        if (updateRemainNumberCount != ticketCategoryCountDtoList.size()) {
            throw new DaMaiFrameException(BaseCode.UPDATE_TICKET_CATEGORY_COUNT_NOT_CORRECT);
        }
    }
    
    private ProgramVo createProgramVo(Long programId){
        ProgramVo programVo = new ProgramVo();
        Program program = 
                Optional.ofNullable(programMapper.selectById(programId))
                        .orElseThrow(() -> new DaMaiFrameException(BaseCode.PROGRAM_NOT_EXIST));
        BeanUtil.copyProperties(program,programVo);
        AreaGetDto areaGetDto = new AreaGetDto();
        areaGetDto.setId(program.getAreaId());
        ApiResponse<AreaVo> areaResponse = baseDataClient.getById(areaGetDto);
        if (Objects.equals(areaResponse.getCode(), ApiResponse.ok().getCode())) {
            if (Objects.nonNull(areaResponse.getData())) {
                programVo.setAreaName(areaResponse.getData().getName());
            }
        }else {
            log.error("base-data rpc getById error areaResponse:{}", JSON.toJSONString(areaResponse));
        }
        return programVo;
    }
    
    private ProgramGroupVo createProgramGroupVo(Long programGroupId){
        ProgramGroupVo programGroupVo = new ProgramGroupVo();
        ProgramGroup programGroup =
                Optional.ofNullable(programGroupMapper.selectById(programGroupId))
                        .orElseThrow(() -> new DaMaiFrameException(BaseCode.PROGRAM_GROUP_NOT_EXIST));
        programGroupVo.setId(programGroup.getId());
        programGroupVo.setProgramSimpleInfoVoList(JSON.parseArray(programGroup.getProgramJson(), ProgramSimpleInfoVo.class));
        programGroupVo.setRecentShowTime(programGroup.getRecentShowTime());
        return programGroupVo;
    }
    
    public List<Long> getAllProgramIdList(){
        LambdaQueryWrapper<Program> programLambdaQueryWrapper =
                Wrappers.lambdaQuery(Program.class).eq(Program::getProgramStatus, BusinessStatus.YES.getCode())
                        .select(Program::getId);
        List<Program> programs = programMapper.selectList(programLambdaQueryWrapper);
        return programs.stream().map(Program::getId).collect(Collectors.toList());
    }
    
    public ProgramVo getDetailFromDb(Long programId) {
        ProgramVo programVo = createProgramVo(programId);
        
        ProgramCategory programCategory = getProgramCategory(programVo.getProgramCategoryId());
        if (Objects.nonNull(programCategory)) {
            programVo.setProgramCategoryName(programCategory.getName());
        }
        ProgramCategory parentProgramCategory = getProgramCategory(programVo.getParentProgramCategoryId());
        if (Objects.nonNull(parentProgramCategory)) {
            programVo.setParentProgramCategoryName(parentProgramCategory.getName());
        }
        
        LambdaQueryWrapper<ProgramShowTime> programShowTimeLambdaQueryWrapper =
                Wrappers.lambdaQuery(ProgramShowTime.class).eq(ProgramShowTime::getProgramId, programId);
        ProgramShowTime programShowTime = Optional.ofNullable(programShowTimeMapper.selectOne(programShowTimeLambdaQueryWrapper))
                .orElseThrow(() -> new DaMaiFrameException(BaseCode.PROGRAM_SHOW_TIME_NOT_EXIST));
        
        programVo.setShowTime(programShowTime.getShowTime());
        programVo.setShowDayTime(programShowTime.getShowDayTime());
        programVo.setShowWeekTime(programShowTime.getShowWeekTime());
        
        return programVo;
    }

    /**
     * 预加载购票人信息
     * @param highHeat
     */
    private void preloadTicketUserList(Integer highHeat){
        if (Objects.equals(highHeat, BusinessStatus.NO.getCode())) {
            return;
        }
        String userId = BaseParameterHolder.getParameter(USER_ID);
        String code = BaseParameterHolder.getParameter(CODE);
        if (StringUtil.isEmpty(userId) || StringUtil.isEmpty(code)) {
            return;
        }
        Boolean userLogin =
                redisCache.hasKey(RedisKeyBuild.createRedisKey(RedisKeyManage.USER_LOGIN, code, userId));
        if (!userLogin) {
            return;
        }
        BusinessThreadPool.execute(() -> {
            try {
                if (!redisCache.hasKey(RedisKeyBuild.createRedisKey(RedisKeyManage.TICKET_USER_LIST,userId))) {
                    TicketUserListDto ticketUserListDto = new TicketUserListDto();
                    ticketUserListDto.setUserId(Long.parseLong(userId));
                    ApiResponse<List<TicketUserVo>> apiResponse = userClient.list(ticketUserListDto);
                    if (Objects.equals(apiResponse.getCode(), BaseCode.SUCCESS.getCode())) {
                        Optional.ofNullable(apiResponse.getData()).filter(CollectionUtil::isNotEmpty)
                                .ifPresent(ticketUserVoList -> redisCache.set(RedisKeyBuild.createRedisKey(
                                        RedisKeyManage.TICKET_USER_LIST,userId),ticketUserVoList));
                    }else {
                        log.warn("userClient.select 调用失败 apiResponse : {}",JSON.toJSONString(apiResponse));
                    }
                }
                
            }catch (Exception e) {
                log.error("预热加载购票人列表失败",e);
            }
        });
    }

    /**
     * 预加载账户订单
     * @param programId
     */
    private void preloadAccountOrderCount(Long programId){
        String userId = BaseParameterHolder.getParameter(USER_ID);
        String code = BaseParameterHolder.getParameter(CODE);
        if (StringUtil.isEmpty(userId) || StringUtil.isEmpty(code)) {
            return;
        }
        Boolean userLogin =
                redisCache.hasKey(RedisKeyBuild.createRedisKey(RedisKeyManage.USER_LOGIN, code, userId));
        if (!userLogin) {
            return;
        }
        BusinessThreadPool.execute(() -> {
            try {
                if (!redisCache.hasKey(RedisKeyBuild.createRedisKey(RedisKeyManage.ACCOUNT_ORDER_COUNT,userId,programId))) {
                    AccountOrderCountDto accountOrderCountDto = new AccountOrderCountDto();
                    accountOrderCountDto.setUserId(Long.parseLong(userId));
                    accountOrderCountDto.setProgramId(programId);
                    ApiResponse<AccountOrderCountVo> apiResponse = orderClient.accountOrderCount(accountOrderCountDto);
                    if (Objects.equals(apiResponse.getCode(), BaseCode.SUCCESS.getCode())) {
                        Optional.ofNullable(apiResponse.getData())
                                .ifPresent(accountOrderCountVo -> redisCache.set(
                                        RedisKeyBuild.createRedisKey(RedisKeyManage.ACCOUNT_ORDER_COUNT,userId,programId),
                                        accountOrderCountVo.getCount(), tokenExpireManager.getTokenExpireTime() + 1,
                                        TimeUnit.MINUTES));
                    }else {
                        log.warn("orderClient.accountOrderCount 调用失败 apiResponse : {}",JSON.toJSONString(apiResponse));
                    }
                }
            }catch (Exception e) {
                log.error("预热加载账户订单数量失败",e);
            }
        });
    }
    
    public ProgramCategory getProgramCategoryMultipleCache(Long programCategoryId){
        return localCacheProgramCategory.get(String.valueOf(programCategoryId),
                key -> getProgramCategory(programCategoryId));
    }
    
    public ProgramCategory getProgramCategory(Long programCategoryId){
        return programCategoryService.getProgramCategory(programCategoryId);
    }
    
    @Transactional(rollbackFor = Exception.class)
    public Boolean resetExecute(ProgramResetExecuteDto programResetExecuteDto) {
        Long programId = programResetExecuteDto.getProgramId();
        LambdaQueryWrapper<Seat> seatQueryWrapper =
                Wrappers.lambdaQuery(Seat.class).eq(Seat::getProgramId, programId)
                        .in(Seat::getSellStatus,SellStatus.LOCK.getCode(),SellStatus.SOLD.getCode());
        List<Seat> seatList = seatMapper.selectList(seatQueryWrapper);
        if (CollectionUtil.isNotEmpty(seatList)) {
            LambdaUpdateWrapper<Seat> seatUpdateWrapper =
                    Wrappers.lambdaUpdate(Seat.class).eq(Seat::getProgramId, programId);
            Seat seatUpdate = new Seat();
            seatUpdate.setSellStatus(SellStatus.NO_SOLD.getCode());
            seatMapper.update(seatUpdate,seatUpdateWrapper);
        }
        LambdaQueryWrapper<TicketCategory> ticketCategoryQueryWrapper =
                Wrappers.lambdaQuery(TicketCategory.class).eq(TicketCategory::getProgramId, programId);
        List<TicketCategory> ticketCategories = ticketCategoryMapper.selectList(ticketCategoryQueryWrapper);
        if (CollectionUtil.isNotEmpty(ticketCategories)) {
            for (TicketCategory ticketCategory : ticketCategories) {
                Long remainNumber = ticketCategory.getRemainNumber();
                Long totalNumber = ticketCategory.getTotalNumber();
                if (!(remainNumber.equals(totalNumber))) {
                    TicketCategory ticketCategoryUpdate = new TicketCategory();
                    ticketCategoryUpdate.setRemainNumber(totalNumber);
                    
                    LambdaUpdateWrapper<TicketCategory> ticketCategoryUpdateWrapper =
                            Wrappers.lambdaUpdate(TicketCategory.class)
                                    .eq(TicketCategory::getProgramId, programId)
                                    .eq(TicketCategory::getId,ticketCategory.getId());
                    ticketCategoryMapper.update(ticketCategoryUpdate,ticketCategoryUpdateWrapper);
                }
            }
        }
        delRedisData(programId);
        delLocalCache(programId);
        return true;
    }
    
    public void delRedisData(Long programId){
        Program program = Optional.ofNullable(programMapper.selectById(programId))
                .orElseThrow(() -> new DaMaiFrameException(BaseCode.PROGRAM_NOT_EXIST));
        List<String> keys = new ArrayList<>();
        keys.add(RedisKeyBuild.createRedisKey(RedisKeyManage.PROGRAM,programId).getRelKey());
        keys.add(RedisKeyBuild.createRedisKey(RedisKeyManage.PROGRAM_GROUP,program.getProgramGroupId()).getRelKey());
        keys.add(RedisKeyBuild.createRedisKey(RedisKeyManage.PROGRAM_SHOW_TIME,programId).getRelKey());
        keys.add(RedisKeyBuild.createRedisKey(RedisKeyManage.PROGRAM_SEAT_NO_SOLD_RESOLUTION_HASH, programId,"*").getRelKey());
        keys.add(RedisKeyBuild.createRedisKey(RedisKeyManage.PROGRAM_SEAT_LOCK_RESOLUTION_HASH, programId,"*").getRelKey());
        keys.add(RedisKeyBuild.createRedisKey(RedisKeyManage.PROGRAM_SEAT_SOLD_RESOLUTION_HASH, programId,"*").getRelKey());
        keys.add(RedisKeyBuild.createRedisKey(RedisKeyManage.PROGRAM_TICKET_CATEGORY_LIST, programId).getRelKey());
        keys.add(RedisKeyBuild.createRedisKey(RedisKeyManage.PROGRAM_TICKET_REMAIN_NUMBER_HASH_RESOLUTION, programId,"*").getRelKey());
        programDelCacheData.del(keys,new String[]{});
    }
    
    public Boolean invalid(final ProgramInvalidDto programInvalidDto) {
        Program program = new Program();
        program.setId(programInvalidDto.getId());
        //修改数据库中的节目状态为下线状态
        program.setProgramStatus(BusinessStatus.NO.getCode());
        int result = programMapper.updateById(program);
        if (result > 0) {
            //删除Redis的缓存
            delRedisData(programInvalidDto.getId());
            //向RedisStream发送消息
            redisStreamPushHandler.push(String.valueOf(programInvalidDto.getId()));
            //删除elasticsearch中的数据
            programEs.deleteByProgramId(programInvalidDto.getId());
            return true;
        }else {
            return false;
        }
    }
    
    public ProgramVo localDetail(final ProgramGetDto programGetDto) {
        return localCacheProgram.getCache(String.valueOf(programGetDto.getId()));
    }
    
    public void delLocalCache(Long programId){
        log.info("删除本地缓存 programId : {}",programId);
        localCacheProgram.del(RedisKeyBuild.createRedisKey(RedisKeyManage.PROGRAM, programId).getRelKey());
        localCacheProgramGroup.del(RedisKeyBuild.createRedisKey(RedisKeyManage.PROGRAM_GROUP, programId).getRelKey());
        localCacheProgramShowTime.del(RedisKeyBuild.createRedisKey(RedisKeyManage.PROGRAM_SHOW_TIME, programId).getRelKey());
        localCacheTicketCategory.del(programId);
    }
}

