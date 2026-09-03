package com.damai.service.strategy;

import cn.hutool.core.collection.CollectionUtil;
import cn.hutool.core.util.StrUtil;
import com.damai.dto.ProgramOrderCreateDto;
import com.damai.dto.SeatDto;
import com.damai.locallock.LocalLockCache;
import com.damai.lock.LockTask;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.locks.ReentrantLock;
import java.util.stream.Collectors;

/**
 * 这个BaseProgramOrder类对本地锁的逻辑进行了封装，但还存在一些可以优化的地方。
 * @program: 极度真实还原大麦网高并发实战项目。 添加 阿星不是程序员 微信，添加时备注 大麦 来获取项目的完整资料 
 * @description: 节目订单
 * @author: 阿星不是程序员
 **/
@Slf4j
@Component
public class BaseProgramOrder {
    
    @Autowired
    private LocalLockCache localLockCache;
    /**
     * 使用本地锁创建订单（基础版本）
     */
    public String localLockCreateOrder(String lockKeyPrefix, ProgramOrderCreateDto programOrderCreateDto, 
                                          LockTask<String> lockTask){
        //提取票种ID列表
        List<SeatDto> seatDtoList = programOrderCreateDto.getSeatDtoList();
        List<Long> ticketCategoryIdList = new ArrayList<>();
        //获取票档列表
        /**
         *  .map(SeatDto::getTicketCategoryId)  // 1. 提取票种ID
         *  .distinct()                         // 2. 去重
         *  .sorted()
         */
        if (CollectionUtil.isNotEmpty(seatDtoList)) {
            ticketCategoryIdList =
                    seatDtoList.stream().map(SeatDto::getTicketCategoryId).distinct().sorted().collect(Collectors.toList());
        }else {
            ticketCategoryIdList.add(programOrderCreateDto.getTicketCategoryId());
        }
        //准备锁对象列表
        //存储所有需要获取的锁对象
        List<ReentrantLock> localLockList = new ArrayList<>(ticketCategoryIdList.size());
        //成功获取的锁列表，用于finally中释放
        List<ReentrantLock> localLockSuccessList = new ArrayList<>(ticketCategoryIdList.size());
        //遍历创建锁
        for (Long ticketCategoryId : ticketCategoryIdList) {
            String lockKey = StrUtil.join("-",lockKeyPrefix,
                    programOrderCreateDto.getProgramId(),ticketCategoryId);
            ReentrantLock localLock = localLockCache.getLock(lockKey,false);
            localLockList.add(localLock);
        }
        //获取锁
        for (ReentrantLock reentrantLock : localLockList) {
            try {
                reentrantLock.lock();
            }catch (Throwable t) {
                break;
            }
            localLockSuccessList.add(reentrantLock);
        }
        //执行业务逻辑
        try {
            return lockTask.execute();
        }finally {
            //你逆序遍历 确保锁一定会被释放
            for (int i = localLockSuccessList.size() - 1; i >= 0; i--) {
                ReentrantLock reentrantLock = localLockSuccessList.get(i);
                try {
                    reentrantLock.unlock();
                }catch (Throwable t) {
                    log.error("local lock unlock error",t);
                }
            }
        }
    }
}
