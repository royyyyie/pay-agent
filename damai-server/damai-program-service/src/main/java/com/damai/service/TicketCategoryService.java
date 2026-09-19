package com.damai.service;


import cn.hutool.core.bean.BeanUtil;
import cn.hutool.core.collection.CollectionUtil;
import com.baidu.fsg.uid.UidGenerator;
import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.core.toolkit.Wrappers;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.damai.core.RedisKeyManage;
import com.damai.dto.TicketCategoryAddDto;
import com.damai.dto.TicketCategoryDto;
import com.damai.dto.TicketCategoryListByProgramDto;
import com.damai.entity.Program;
import com.damai.entity.TicketCategory;
import com.damai.enums.BaseCode;
import com.damai.exception.DaMaiFrameException;
import com.damai.mapper.ProgramMapper;
import com.damai.mapper.TicketCategoryMapper;
import com.damai.redis.RedisCache;
import com.damai.redis.RedisKeyBuild;
import com.damai.service.cache.local.LocalCacheTicketCategory;
import com.damai.servicelock.LockType;
import com.damai.servicelock.annotion.ServiceLock;
import com.damai.util.DateUtils;
import com.damai.util.ServiceLockTool;
import com.damai.vo.TicketCategoryDetailVo;
import com.damai.vo.TicketCategoryVo;
import lombok.extern.slf4j.Slf4j;
import org.redisson.api.RLock;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.Date;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.concurrent.TimeUnit;
import java.util.stream.Collectors;

import static com.damai.core.DistributedLockConstants.GET_REMAIN_NUMBER_LOCK;
import static com.damai.core.DistributedLockConstants.GET_TICKET_CATEGORY_LOCK;
import static com.damai.core.DistributedLockConstants.REMAIN_NUMBER_LOCK;
import static com.damai.core.DistributedLockConstants.TICKET_CATEGORY_LOCK;

/**
 * @program: 极度真实还原大麦网高并发实战项目。 添加 阿星不是程序员 微信，添加时备注 大麦 来获取项目的完整资料 
 * @description: 票档 service
 * @author: 阿星不是程序员
 **/
@Slf4j
@Service
public class TicketCategoryService extends ServiceImpl<TicketCategoryMapper, TicketCategory> {
    
    @Autowired
    private UidGenerator uidGenerator;
    
    @Autowired
    private RedisCache redisCache;
    
    @Autowired
    private TicketCategoryMapper ticketCategoryMapper;
    
    @Autowired
    private ServiceLockTool serviceLockTool;
    
    @Autowired
    private LocalCacheTicketCategory localCacheTicketCategory;
    
    @Autowired
    private ProgramMapper programMapper;
    
    @Transactional(rollbackFor = Exception.class)
    public Long add(TicketCategoryAddDto ticketCategoryAddDto) {
        Program program = programMapper.selectById(ticketCategoryAddDto.getProgramId());
        if (Objects.isNull(program)) {
            throw new DaMaiFrameException(BaseCode.PROGRAM_NOT_EXIST);
        }
        TicketCategory ticketCategory = new TicketCategory();
        BeanUtil.copyProperties(ticketCategoryAddDto,ticketCategory);
        ticketCategory.setId(uidGenerator.getUid());
        ticketCategoryMapper.insert(ticketCategory);
        return ticketCategory.getId();
    }
    
    public List<TicketCategoryVo> selectTicketCategoryListByProgramIdMultipleCache(Long programId, Date showTime){
        return localCacheTicketCategory.getCache(programId,key -> selectTicketCategoryListByProgramId(programId, 
                DateUtils.countBetweenSecond(DateUtils.now(),showTime), TimeUnit.SECONDS));
    }
    
    @ServiceLock(lockType= LockType.Read,name = TICKET_CATEGORY_LOCK,keys = {"#programId"})
    public List<TicketCategoryVo> selectTicketCategoryListByProgramId(Long programId,Long expireTime,TimeUnit timeUnit){
        //从缓存中查询
        List<TicketCategoryVo> ticketCategoryVoList =
                redisCache.getValueIsList(RedisKeyBuild.createRedisKey(RedisKeyManage.PROGRAM_TICKET_CATEGORY_LIST, 
                        programId), TicketCategoryVo.class);
        //如果缓存中存在直接返回数据
        if (CollectionUtil.isNotEmpty(ticketCategoryVoList)) {
            return ticketCategoryVoList;
        }
        //加锁
        RLock lock = serviceLockTool.getLock(LockType.Reentrant, GET_TICKET_CATEGORY_LOCK,
                new String[]{String.valueOf(programId)});
        lock.lock();
        try {
            //再从缓存中查询，如果缓存不存在则从数据库中查询再放入到缓存中
            return redisCache.getValueIsList(
                    RedisKeyBuild.createRedisKey(RedisKeyManage.PROGRAM_TICKET_CATEGORY_LIST, programId),
                    TicketCategoryVo.class,
                    () -> {
                        LambdaQueryWrapper<TicketCategory> ticketCategoryLambdaQueryWrapper =
                                Wrappers.lambdaQuery(TicketCategory.class).eq(TicketCategory::getProgramId, programId);
                        List<TicketCategory> ticketCategoryList =
                                ticketCategoryMapper.selectList(ticketCategoryLambdaQueryWrapper);
                        return ticketCategoryList.stream().map(ticketCategory -> {
                            ticketCategory.setRemainNumber(null);
                            TicketCategoryVo ticketCategoryVo = new TicketCategoryVo();
                            BeanUtil.copyProperties(ticketCategory, ticketCategoryVo);
                            return ticketCategoryVo;
                        }).collect(Collectors.toList());
                    }, expireTime, timeUnit);
        }finally {
            //解锁
            lock.unlock();
        }
    }

    /**
     * 票务库存余量查询的缓存实现
     * @param programId
     * @param ticketCategoryId
     * @return
     */
    //依旧是读写锁，防止在查询库存时，库存被修改导致数据不一致，但允许多个读操作并发执行，阻塞写操作
    @ServiceLock(lockType= LockType.Read,name = REMAIN_NUMBER_LOCK,keys = {"#programId","#ticketCategoryId"})
    public Map<String, Long> getRedisRemainNumberResolution(Long programId,Long ticketCategoryId){
        //第一此检查缓存是否存在
        Map<String, Long> ticketCategoryRemainNumber =
                redisCache.getAllMapForHash(RedisKeyBuild.createRedisKey(RedisKeyManage.PROGRAM_TICKET_REMAIN_NUMBER_HASH_RESOLUTION,
                        programId,ticketCategoryId), Long.class);
        
        if (CollectionUtil.isNotEmpty(ticketCategoryRemainNumber)) {
            return ticketCategoryRemainNumber;
        }
        //如果不存在上锁进入数据库查询
        //获取互斥锁 防止多个线程同时查询数据库加载库存 避免缓存击穿问题 保证库存数据的一致性
        RLock lock = serviceLockTool.getLock(LockType.Reentrant, GET_REMAIN_NUMBER_LOCK,
                new String[]{String.valueOf(programId),String.valueOf(ticketCategoryId)});
        lock.lock();
        try {
            //第二次缓存检查
            ticketCategoryRemainNumber =
                    redisCache.getAllMapForHash(RedisKeyBuild.createRedisKey(
                            RedisKeyManage.PROGRAM_TICKET_REMAIN_NUMBER_HASH_RESOLUTION, programId,ticketCategoryId), Long.class);
            if (CollectionUtil.isNotEmpty(ticketCategoryRemainNumber)) {
                return ticketCategoryRemainNumber;
            }
            //继续查询数据库 查询该节目下的该票档的信息
            LambdaQueryWrapper<TicketCategory> ticketCategoryLambdaQueryWrapper = Wrappers.lambdaQuery(TicketCategory.class)
                    .eq(TicketCategory::getProgramId, programId).eq(TicketCategory::getId,ticketCategoryId);
            List<TicketCategory> ticketCategoryList = ticketCategoryMapper.selectList(ticketCategoryLambdaQueryWrapper);
            //数据转换和缓存
            Map<String, Long> map = ticketCategoryList.stream().collect(Collectors.toMap(t -> String.valueOf(t.getId()),
                    TicketCategory::getRemainNumber, (v1, v2) -> v2));
            redisCache.putHash(RedisKeyBuild.createRedisKey(RedisKeyManage.PROGRAM_TICKET_REMAIN_NUMBER_HASH_RESOLUTION,
                    programId,ticketCategoryId),map);
            return map;
        }finally {
            lock.unlock();
        }
    }
    
    public TicketCategoryDetailVo detail(TicketCategoryDto ticketCategoryDto) {
        TicketCategory ticketCategory = ticketCategoryMapper.selectById(ticketCategoryDto.getId());
        TicketCategoryDetailVo ticketCategoryDetailVo = new TicketCategoryDetailVo();
        BeanUtil.copyProperties(ticketCategory,ticketCategoryDetailVo);
        return ticketCategoryDetailVo;
    }
    
    public List<TicketCategoryDetailVo> selectListByProgram(TicketCategoryListByProgramDto ticketCategoryListByProgramDto) {
        List<TicketCategory> ticketCategorieList = ticketCategoryMapper.selectList(Wrappers.lambdaQuery(TicketCategory.class)
                .eq(TicketCategory::getProgramId, ticketCategoryListByProgramDto.getProgramId()));
        return ticketCategorieList.stream().map(ticketCategory -> {
            TicketCategoryDetailVo ticketCategoryDetailVo = new TicketCategoryDetailVo();
            BeanUtil.copyProperties(ticketCategory,ticketCategoryDetailVo);
            return ticketCategoryDetailVo;
        }).collect(Collectors.toList());
    }

    /**
     * 为有界推荐候选批量读取票档，避免逐节目查询造成 N+1。
     */
    public Map<Long, List<TicketCategoryDetailVo>> selectListByPrograms(List<Long> programIds) {
        List<Long> uniqueProgramIds = programIds.stream()
                .filter(Objects::nonNull)
                .distinct()
                .limit(10)
                .collect(Collectors.toList());
        if (uniqueProgramIds.isEmpty()) {
            return Map.of();
        }
        List<TicketCategory> ticketCategories = ticketCategoryMapper.selectList(
                Wrappers.lambdaQuery(TicketCategory.class)
                        .in(TicketCategory::getProgramId, uniqueProgramIds));
        Map<Long, List<TicketCategoryDetailVo>> result = new LinkedHashMap<>();
        for (TicketCategory ticketCategory : ticketCategories) {
            TicketCategoryDetailVo detail = new TicketCategoryDetailVo();
            BeanUtil.copyProperties(ticketCategory, detail);
            result.computeIfAbsent(detail.getProgramId(), ignored -> new ArrayList<>())
                    .add(detail);
        }
        return result;
    }
}
