package com.damai.service;


import cn.hutool.core.bean.BeanUtil;
import cn.hutool.core.collection.CollectionUtil;
import com.baidu.fsg.uid.UidGenerator;
import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.core.toolkit.Wrappers;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.damai.core.RedisKeyManage;
import com.damai.dto.TicketUserDto;
import com.damai.dto.TicketUserIdDto;
import com.damai.dto.TicketUserListDto;
import com.damai.entity.TicketUser;
import com.damai.entity.User;
import com.damai.enums.BaseCode;
import com.damai.exception.DaMaiFrameException;
import com.damai.mapper.TicketUserMapper;
import com.damai.mapper.UserMapper;
import com.damai.redis.RedisCache;
import com.damai.redis.RedisKeyBuild;
import com.damai.vo.TicketUserVo;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;
import java.util.Objects;

/**
 * @program: 极度真实还原大麦网高并发实战项目。 添加 阿星不是程序员 微信，添加时备注 大麦 来获取项目的完整资料 
 * @description: 购票人 service
 * @author: 阿星不是程序员
 **/
@Service
public class TicketUserService extends ServiceImpl<TicketUserMapper, TicketUser> {
    
    @Autowired
    private TicketUserMapper ticketUserMapper;
    
    @Autowired
    private UserMapper userMapper;
    
    @Autowired
    private UidGenerator uidGenerator;
    
    @Autowired
    private RedisCache redisCache;
    
    public List<TicketUserVo> list(TicketUserListDto ticketUserListDto) {
        //先从缓存中查询
        List<TicketUserVo> ticketUserVoList = redisCache.getValueIsList(RedisKeyBuild.createRedisKey(
                RedisKeyManage.TICKET_USER_LIST, ticketUserListDto.getUserId()), TicketUserVo.class);
        if (CollectionUtil.isNotEmpty(ticketUserVoList)) {
            return ticketUserVoList;
        }
        //从数据库中查
        LambdaQueryWrapper<TicketUser> ticketUserLambdaQueryWrapper = Wrappers.lambdaQuery(TicketUser.class)
                .eq(TicketUser::getUserId, ticketUserListDto.getUserId());
        List<TicketUser> ticketUsers = ticketUserMapper.selectList(ticketUserLambdaQueryWrapper);
        return BeanUtil.copyToList(ticketUsers,TicketUserVo.class);
    }
    
    @Transactional(rollbackFor = Exception.class)
    public void add(TicketUserDto ticketUserDto) {
        //获取当前用户信息
        User user = userMapper.selectById(ticketUserDto.getUserId());
        //如果不存在，直接抛出异常
        if (Objects.isNull(user)) {
            throw new DaMaiFrameException(BaseCode.USER_EMPTY);
        }
        //从数据库中查询该购票人信息
        LambdaQueryWrapper<TicketUser> ticketUserLambdaQueryWrapper = Wrappers.lambdaQuery(TicketUser.class)
                .eq(TicketUser::getUserId, ticketUserDto.getUserId())
                .eq(TicketUser::getIdType, ticketUserDto.getIdType())
                .eq(TicketUser::getIdNumber, ticketUserDto.getIdNumber());
        TicketUser ticketUser = ticketUserMapper.selectOne(ticketUserLambdaQueryWrapper);
        //存在则抛出异常
        if (Objects.nonNull(ticketUser)) {
            throw new DaMaiFrameException(BaseCode.TICKET_USER_EXIST);
        }

        TicketUser addTicketUser = new TicketUser();
        BeanUtil.copyProperties(ticketUserDto,addTicketUser);
        addTicketUser.setId(uidGenerator.getUid());//分布式id生成器生成id

        ticketUserMapper.insert(addTicketUser);
        //缓存清理，删除该用户的购票人列表缓存，确保下次查询时获得最新的数据，保证缓存与数据库的一致性
        delTicketUserVoListCache(String.valueOf(ticketUserDto.getUserId()));
    }
    //删除购票人
    @Transactional(rollbackFor = Exception.class)
    public void delete(TicketUserIdDto ticketUserIdDto) {
        TicketUser ticketUser = ticketUserMapper.selectById(ticketUserIdDto.getId());
        if (Objects.isNull(ticketUser)) {
            throw new DaMaiFrameException(BaseCode.TICKET_USER_EMPTY);
        }
        //删除数据库中的购票人
        ticketUserMapper.deleteById(ticketUserIdDto.getId());
        //删除缓存中购票人
        delTicketUserVoListCache(String.valueOf(ticketUser.getUserId()));
    }
    
    public void delTicketUserVoListCache(String userId){
        redisCache.del(RedisKeyBuild.createRedisKey(RedisKeyManage.TICKET_USER_LIST, userId));
    }
}
