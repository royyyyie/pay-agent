package com.damai.service;

import cn.hutool.core.collection.CollectionUtil;
import com.alibaba.fastjson.JSON;
import com.alibaba.fastjson.JSONArray;
import com.alibaba.fastjson.JSONObject;
import com.baidu.fsg.uid.UidGenerator;
import com.damai.client.OrderClient;
import com.damai.common.ApiResponse;
import com.damai.core.RedisKeyManage;
import com.damai.dto.DelayOrderCancelDto;
import com.damai.dto.OrderCreateDto;
import com.damai.dto.OrderTicketUserCreateDto;
import com.damai.dto.ProgramOrderCreateDto;
import com.damai.dto.SeatDto;
import com.damai.entity.ProgramShowTime;
import com.damai.enums.BaseCode;
import com.damai.enums.OrderStatus;
import com.damai.enums.SellStatus;
import com.damai.exception.DaMaiFrameException;
import com.damai.redis.RedisKeyBuild;
import com.damai.service.delaysend.DelayOrderCancelSend;
import com.damai.service.kafka.CreateOrderMqDomain;
import com.damai.service.kafka.CreateOrderSend;
import com.damai.service.lua.ProgramCacheCreateOrderData;
import com.damai.service.lua.ProgramCacheCreateOrderResolutionOperate;
import com.damai.service.lua.ProgramCacheResolutionOperate;
import com.damai.service.tool.SeatMatch;
import com.damai.util.DateUtils;
import com.damai.vo.ProgramVo;
import com.damai.vo.SeatVo;
import com.damai.vo.TicketCategoryVo;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.Date;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Map.Entry;
import java.util.Objects;
import java.util.Optional;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.stream.Collectors;

import static com.damai.service.constant.ProgramOrderConstant.ORDER_TABLE_COUNT;

/**
 * @program: 极度真实还原大麦网高并发实战项目。 添加 阿星不是程序员 微信，添加时备注 大麦 来获取项目的完整资料 
 * @description: 节目订单 service
 * @author: 阿星不是程序员
 **/
@Slf4j
@Service
public class ProgramOrderService {
    
    @Autowired
    private OrderClient orderClient;
    
    @Autowired
    private UidGenerator uidGenerator;
    
    @Autowired
    private ProgramCacheResolutionOperate programCacheResolutionOperate;
    
    @Autowired
    ProgramCacheCreateOrderResolutionOperate programCacheCreateOrderResolutionOperate;
    
    @Autowired
    private DelayOrderCancelSend delayOrderCancelSend;
    
    @Autowired
    private CreateOrderSend createOrderSend;
    
    @Autowired
    private ProgramService programService;
    
    @Autowired
    private ProgramShowTimeService programShowTimeService;
    
    @Autowired
    private TicketCategoryService ticketCategoryService;
    
    @Autowired
    private SeatService seatService;

    /**
     * 验证并获取票档及票档信息
     * @param programOrderCreateDto
     * @param showTime
     * @return
     */
    public List<TicketCategoryVo> getTicketCategoryList(ProgramOrderCreateDto programOrderCreateDto, Date showTime){
        List<TicketCategoryVo> getTicketCategoryVoList = new ArrayList<>();
        //获取后台数据中的票档信息
        List<TicketCategoryVo> ticketCategoryVoList =
                ticketCategoryService.selectTicketCategoryListByProgramIdMultipleCache(programOrderCreateDto.getProgramId(),
                        showTime);
        Map<Long, TicketCategoryVo> ticketCategoryVoMap =
                ticketCategoryVoList.stream()
                        .collect(Collectors.toMap(TicketCategoryVo::getId, ticketCategoryVo -> ticketCategoryVo));
        //手动选座：验证该座位票档是否存在
        List<SeatDto> seatDtoList = programOrderCreateDto.getSeatDtoList();
        if (CollectionUtil.isNotEmpty(seatDtoList)) {
            for (SeatDto seatDto : seatDtoList) {
                TicketCategoryVo ticketCategoryVo = ticketCategoryVoMap.get(seatDto.getTicketCategoryId());
                if (Objects.nonNull(ticketCategoryVo)) {
                    getTicketCategoryVoList.add(ticketCategoryVo);
                }else {
                    throw new DaMaiFrameException(BaseCode.TICKET_CATEGORY_NOT_EXIST_V2);
                }
            }
        } else {
            //自动选座，当前选取的票档是否存在与数据库中
            TicketCategoryVo ticketCategoryVo = ticketCategoryVoMap.get(programOrderCreateDto.getTicketCategoryId());
            if (Objects.nonNull(ticketCategoryVo)) {
                getTicketCategoryVoList.add(ticketCategoryVo);
            }else {
                throw new DaMaiFrameException(BaseCode.TICKET_CATEGORY_NOT_EXIST_V2);
            }
        }
        //返回票档相关信息列表列表
        return getTicketCategoryVoList;
    }

    /**
     * 在之前完成了如下的验证：
     * ●  验证座位参数
     * ●  将节目缓存
     * ●  验证缓存是否存在节目数据
     * ●  验证缓存是否存在演出时间数据
     * ●  验证用户是否存在
     * 接下来时开始执行真正的业务流程处理环节
     * @param programOrderCreateDto
     * @return
     */
    public String create(ProgramOrderCreateDto programOrderCreateDto) {
        //从多级缓存中查找节目演出时间ProgramShowTime，忧郁已经到订单业务，所以直接从redis中查询
        ProgramShowTime programShowTime =
                programShowTimeService.selectProgramShowTimeByProgramIdMultipleCache(programOrderCreateDto.getProgramId());
        //查询对应的票档类型
        List<TicketCategoryVo> getTicketCategoryList =
                getTicketCategoryList(programOrderCreateDto,programShowTime.getShowTime());
        //传入的座位总价格
        BigDecimal parameterOrderPrice = new BigDecimal("0");
        //库中的座位总价格
        BigDecimal databaseOrderPrice = new BigDecimal("0");
        //要购买的座位
        List<SeatVo> purchaseSeatList = new ArrayList<>();
        //入参的座位
        List<SeatDto> seatDtoList = programOrderCreateDto.getSeatDtoList();
        //该节目下所有未售卖的座位
        List<SeatVo> seatVoList = new ArrayList<>();
        //该节目下的余票数量
        Map<String, Long> ticketCategoryRemainNumber = new HashMap<>(16);
        //遍历得到的票档
        for (TicketCategoryVo ticketCategory : getTicketCategoryList) {
            //获取所有座位信息
            List<SeatVo> allSeatVoList =
                    seatService.selectSeatResolution(programOrderCreateDto.getProgramId(), ticketCategory.getId(), 
                            DateUtils.countBetweenSecond(DateUtils.now(), programShowTime.getShowTime()), TimeUnit.SECONDS);
            //获取未售卖的座位信息
            seatVoList.addAll(allSeatVoList.stream().
                    filter(seatVo -> seatVo.getSellStatus().equals(SellStatus.NO_SOLD.getCode())).toList());
            //将查询到的余票数量放入ticketCategoryRemainNumber   key:票档id  value:余票数量
            ticketCategoryRemainNumber.putAll(ticketCategoryService.getRedisRemainNumberResolution(
                    programOrderCreateDto.getProgramId(),ticketCategory.getId()));
        }
        //入参座位存在
        if (CollectionUtil.isNotEmpty(seatDtoList)) {
            //余票数量检测 key：票档id  value：票档下座位数量
            Map<Long, Long> seatTicketCategoryDtoCount = seatDtoList.stream()
                    .collect(Collectors.groupingBy(SeatDto::getTicketCategoryId, Collectors.counting()));
            for (Entry<Long, Long> entry : seatTicketCategoryDtoCount.entrySet()) {
                Long ticketCategoryId = entry.getKey();
                Long purchaseCount = entry.getValue();
                //余票数量
                Long remainNumber = Optional.ofNullable(ticketCategoryRemainNumber.get(String.valueOf(ticketCategoryId)))
                        .orElseThrow(() -> new DaMaiFrameException(BaseCode.TICKET_CATEGORY_NOT_EXIST_V2));
                //如果购买数量大于余票数量，那么提示数量不足
                if (purchaseCount > remainNumber) {
                    throw new DaMaiFrameException(BaseCode.TICKET_REMAIN_NUMBER_NOT_SUFFICIENT);
                }
            }
            //验证入参的对象在库中的状态，不存在、已锁、已售卖
            Map<String, SeatVo> seatVoMap = seatVoList.stream().collect(Collectors
                    .toMap(seat -> seat.getRowCode() + "-" + seat.getColCode(), seat -> seat, (v1, v2) -> v2));
            for (SeatDto seatDto : seatDtoList) {
                SeatVo seatVo = seatVoMap.get(seatDto.getRowCode() + "-" + seatDto.getColCode());
                if (Objects.isNull(seatVo)) {
                    throw new DaMaiFrameException(BaseCode.SEAT_IS_NOT_NOT_SOLD);
                }
                purchaseSeatList.add(seatVo);
                //将入参的座位价格进行累加
                parameterOrderPrice = parameterOrderPrice.add(seatDto.getPrice());
                //将库中的座位价格进行类型
                databaseOrderPrice = databaseOrderPrice.add(seatVo.getPrice());
            }
            //传入的座位价格累加不能大于存放的相应座位累加价格
            if (parameterOrderPrice.compareTo(databaseOrderPrice) > 0) {
                throw new DaMaiFrameException(BaseCode.PRICE_ERROR);
            }
        }else {
            //入参座位不存在，利用算法自动根据人数和票档进行分配相邻座位
            Long ticketCategoryId = programOrderCreateDto.getTicketCategoryId();
            Integer ticketCount = programOrderCreateDto.getTicketCount();
            //余票检测
            Long remainNumber = Optional.ofNullable(ticketCategoryRemainNumber.get(String.valueOf(ticketCategoryId)))
                    .orElseThrow(() -> new DaMaiFrameException(BaseCode.TICKET_CATEGORY_NOT_EXIST_V2));
            //如果购票的数量大于余票数量，则直接抛出异常提示
            if (ticketCount > remainNumber) {
                throw new DaMaiFrameException(BaseCode.TICKET_REMAIN_NUMBER_NOT_SUFFICIENT);
            }
            //用算法匹配座位
            purchaseSeatList = SeatMatch.findAdjacentSeatVos(seatVoList.stream().filter(seatVo ->
                    Objects.equals(seatVo.getTicketCategoryId(), ticketCategoryId)).collect(Collectors.toList()), ticketCount);
            //如果匹配出来的座位数量小于要购买的数量，拒绝执行
            if (purchaseSeatList.size() < ticketCount) {
                throw new DaMaiFrameException(BaseCode.SEAT_OCCUPY);
            }
        }
        //更新缓存余票数量和修改座位状态
        updateProgramCacheDataResolution(programOrderCreateDto.getProgramId(),purchaseSeatList,OrderStatus.NO_PAY);
        //组装订单数据 向数据库中插入数据 返回订单编号
        return doCreate(programOrderCreateDto,purchaseSeatList);
    }
    
    
    public String createNew(ProgramOrderCreateDto programOrderCreateDto) {
        //修改节目相关数据
        List<SeatVo> purchaseSeatList = createOrderOperateProgramCacheResolution(programOrderCreateDto);
        //将筛选出来的购买的座位信息传入，执行创建订单的操作
        return doCreate(programOrderCreateDto,purchaseSeatList);
    }
    /**
     * ⚠️注意！在升级的大麦pro版本中进行了优化，可以在保证高并发的同时，Redis和数据库的数据也能保证一致性，以及两者详细的扣减记录
     * ✅大麦pro升级功能的详细介绍，请看 <a href="https://javaup.chat/damai/damai-pro/release-intro">...</a>
     * ✨如何获取大麦pro项目？请看 <a href="https://articles.zsxq.com/id_m4d7ni4zwkbq.html">...</a>
     **/
    public String createNewAsync(ProgramOrderCreateDto programOrderCreateDto) {
        List<SeatVo> purchaseSeatList = createOrderOperateProgramCacheResolution(programOrderCreateDto);
        return doCreateV2(programOrderCreateDto,purchaseSeatList);
    }

    /**
     * 缓存预热：提前加载座位和库存数据到缓存
     * 原子性座位锁定：通过Lua脚本原子性锁定座位
     * 库存扣减：同步扣减库存余量
     * 两种下单模式：支持选座下单和按票价分类下单
     * 异步处理：与订单创建主流程解耦
     * @param programOrderCreateDto
     * @return
     */
    public List<SeatVo> createOrderOperateProgramCacheResolution(ProgramOrderCreateDto programOrderCreateDto){
        //从多级缓存中查找节目演出时间ProgramShowTime
        ProgramShowTime programShowTime =
                programShowTimeService.selectProgramShowTimeByProgramIdMultipleCache(programOrderCreateDto.getProgramId());
        //查询对应的票档类型
        List<TicketCategoryVo> getTicketCategoryList =
                getTicketCategoryList(programOrderCreateDto,programShowTime.getShowTime());
        //遍历得到的票档缓存预热
        for (TicketCategoryVo ticketCategory : getTicketCategoryList) {
            //从缓存中查询座位，如果缓存不存在，则从数据库查询后再放入缓存
            seatService.selectSeatResolution(programOrderCreateDto.getProgramId(), ticketCategory.getId(),
                            DateUtils.countBetweenSecond(DateUtils.now(), programShowTime.getShowTime()), TimeUnit.SECONDS);
            //从缓存中查询余票数量，如果缓存不存在，则从数据库查询后再放入缓存
            ticketCategoryService.getRedisRemainNumberResolution(
                    programOrderCreateDto.getProgramId(),ticketCategory.getId());
        }
        //准备Lua脚本参数
        Long programId = programOrderCreateDto.getProgramId();
        List<SeatDto> seatDtoList = programOrderCreateDto.getSeatDtoList();
        List<String> keys = new ArrayList<>();
        String[] data = new String[2];
        //更新票档数据集合
        JSONArray jsonArray = new JSONArray();
        //添加座位数据集合
        JSONArray addSeatDatajsonArray = new JSONArray();
        if (CollectionUtil.isNotEmpty(seatDtoList)) {
            keys.add("1"); //手动选座
            //key:票档id  value：座位
            Map<Long, List<SeatDto>> seatTicketCategoryDtoCount = seatDtoList.stream()
                    .collect(Collectors.groupingBy(SeatDto::getTicketCategoryId));
            //
            for (Entry<Long, List<SeatDto>> entry : seatTicketCategoryDtoCount.entrySet()) {
                Long ticketCategoryId = entry.getKey();
                int ticketCount = entry.getValue().size();
                //这里是计算更新票档数据
                JSONObject jsonObject = new JSONObject();
                jsonObject.put("programTicketRemainNumberHashKey",RedisKeyBuild.createRedisKey(
                        RedisKeyManage.PROGRAM_TICKET_REMAIN_NUMBER_HASH_RESOLUTION, programId, ticketCategoryId).getRelKey());
                jsonObject.put("ticketCategoryId",ticketCategoryId);
                jsonObject.put("ticketCount",ticketCount);
                jsonArray.add(jsonObject);

                // 座位锁定数据
                JSONObject seatDatajsonObject = new JSONObject();
                seatDatajsonObject.put("seatNoSoldHashKey",RedisKeyBuild.createRedisKey(
                        RedisKeyManage.PROGRAM_SEAT_NO_SOLD_RESOLUTION_HASH, programId, ticketCategoryId).getRelKey());
                seatDatajsonObject.put("seatDataList",JSON.toJSONString(entry.getValue()));
                addSeatDatajsonArray.add(seatDatajsonObject);
            }
        }else {
            keys.add("2");
            Long ticketCategoryId = programOrderCreateDto.getTicketCategoryId();
            Integer ticketCount = programOrderCreateDto.getTicketCount();
            JSONObject jsonObject = new JSONObject();
            jsonObject.put("programTicketRemainNumberHashKey",RedisKeyBuild.createRedisKey(
                    RedisKeyManage.PROGRAM_TICKET_REMAIN_NUMBER_HASH_RESOLUTION, programId, ticketCategoryId).getRelKey());
            jsonObject.put("ticketCategoryId",ticketCategoryId);
            jsonObject.put("ticketCount",ticketCount);
            jsonObject.put("seatNoSoldHashKey",RedisKeyBuild.createRedisKey(
                    RedisKeyManage.PROGRAM_SEAT_NO_SOLD_RESOLUTION_HASH, programId, ticketCategoryId).getRelKey());
            jsonArray.add(jsonObject);
        }
        //未售卖座位hash的key(占位符形式)
        keys.add(RedisKeyBuild.getRedisKey(RedisKeyManage.PROGRAM_SEAT_NO_SOLD_RESOLUTION_HASH));
        //锁定座位hash的key(占位符形式)
        keys.add(RedisKeyBuild.getRedisKey(RedisKeyManage.PROGRAM_SEAT_LOCK_RESOLUTION_HASH));
        keys.add(String.valueOf(programOrderCreateDto.getProgramId()));
        //执行Lua脚本
        data[0] = JSON.toJSONString(jsonArray);
        data[1] = JSON.toJSONString(addSeatDatajsonArray);
        ProgramCacheCreateOrderData programCacheCreateOrderData = 
                programCacheCreateOrderResolutionOperate.programCacheOperate(keys, data);
        if (!Objects.equals(programCacheCreateOrderData.getCode(), BaseCode.SUCCESS.getCode())) {
            throw new DaMaiFrameException(Objects.requireNonNull(BaseCode.getRc(programCacheCreateOrderData.getCode())));
        }
        return programCacheCreateOrderData.getPurchaseSeatList();
    }

    /**
     * 真正的创建订单 向数据库中插入相关数据
     * @param programOrderCreateDto
     * @param purchaseSeatList
     * @return
     */
    private String doCreate(ProgramOrderCreateDto programOrderCreateDto,List<SeatVo> purchaseSeatList){
        //主订单参数构建 用于将前端请求参数转换为订单创建DTO
        OrderCreateDto orderCreateDto = buildCreateOrderParam(programOrderCreateDto, purchaseSeatList);
        //创建订单
        String orderNumber = createOrderByRpc(orderCreateDto,purchaseSeatList);
        //延迟队列创建
        DelayOrderCancelDto delayOrderCancelDto = new DelayOrderCancelDto();
        delayOrderCancelDto.setOrderNumber(orderCreateDto.getOrderNumber());
        delayOrderCancelSend.sendMessage(JSON.toJSONString(delayOrderCancelDto));
        //返回订单编号
        return orderNumber;
    }
    
    private String doCreateV2(ProgramOrderCreateDto programOrderCreateDto,List<SeatVo> purchaseSeatList){
        OrderCreateDto orderCreateDto = buildCreateOrderParam(programOrderCreateDto, purchaseSeatList);
        
        String orderNumber = createOrderByMq(orderCreateDto,purchaseSeatList);
        
        DelayOrderCancelDto delayOrderCancelDto = new DelayOrderCancelDto();
        delayOrderCancelDto.setOrderNumber(orderCreateDto.getOrderNumber());
        delayOrderCancelSend.sendMessage(JSON.toJSONString(delayOrderCancelDto));
        
        return orderNumber;
    }

    /**
     * 这是一个订单创建参数构建方法，用于将前端请求参数转换为订单创建DTO
     * 订单基础信息构建：创建订单的基本信息
     * 座位与购票人匹配：将座位与购票人一一对应
     * 价格计算：计算订单总价
     * 订单号生成：生成唯一的订单号
     * 数据结构转换：将前端DTO转换为订单创建DTO
     * @param programOrderCreateDto
     * @param purchaseSeatList
     * @return
     */
    private OrderCreateDto buildCreateOrderParam(ProgramOrderCreateDto programOrderCreateDto,List<SeatVo> purchaseSeatList){
        //获取节目信息
        ProgramVo programVo = programService.simpleGetProgramAndShowMultipleCache(programOrderCreateDto.getProgramId());
        //构建订单基础信息
        OrderCreateDto orderCreateDto = new OrderCreateDto();
        orderCreateDto.setOrderNumber(uidGenerator.getOrderNumber(programOrderCreateDto.getUserId(),ORDER_TABLE_COUNT));// 设置基本信息
        orderCreateDto.setProgramId(programOrderCreateDto.getProgramId());
        orderCreateDto.setProgramItemPicture(programVo.getItemPicture());
        orderCreateDto.setUserId(programOrderCreateDto.getUserId());
        orderCreateDto.setProgramTitle(programVo.getTitle());
        orderCreateDto.setProgramPlace(programVo.getPlace());
        orderCreateDto.setProgramShowTime(programVo.getShowTime());
        orderCreateDto.setProgramPermitChooseSeat(programVo.getPermitChooseSeat());
        //计算订单总价
        BigDecimal databaseOrderPrice =
                purchaseSeatList.stream().map(SeatVo::getPrice).reduce(BigDecimal.ZERO, BigDecimal::add);
        orderCreateDto.setOrderPrice(databaseOrderPrice);
        orderCreateDto.setCreateOrderTime(DateUtils.now());
        //匹配座位和购票人
        List<Long> ticketUserIdList = programOrderCreateDto.getTicketUserIdList();
        List<OrderTicketUserCreateDto> orderTicketUserCreateDtoList = new ArrayList<>();
        for (int i = 0; i < ticketUserIdList.size(); i++) {
            Long ticketUserId = ticketUserIdList.get(i);
            // 创建购票人订单项
            OrderTicketUserCreateDto orderTicketUserCreateDto = new OrderTicketUserCreateDto();
            orderTicketUserCreateDto.setOrderNumber(orderCreateDto.getOrderNumber());
            orderTicketUserCreateDto.setProgramId(programOrderCreateDto.getProgramId());
            orderTicketUserCreateDto.setUserId(programOrderCreateDto.getUserId());
            orderTicketUserCreateDto.setTicketUserId(ticketUserId);
            // 获取对应的座位
            SeatVo seatVo =
                    Optional.ofNullable(purchaseSeatList.get(i))
                            .orElseThrow(() -> new DaMaiFrameException(BaseCode.SEAT_NOT_EXIST));
            // 设置座位信息
            orderTicketUserCreateDto.setSeatId(seatVo.getId());
            orderTicketUserCreateDto.setSeatInfo(seatVo.getRowCode()+"排"+seatVo.getColCode()+"列");
            orderTicketUserCreateDto.setTicketCategoryId(seatVo.getTicketCategoryId());
            orderTicketUserCreateDto.setOrderPrice(seatVo.getPrice());
            orderTicketUserCreateDto.setCreateOrderTime(DateUtils.now());
            orderTicketUserCreateDtoList.add(orderTicketUserCreateDto);
        }
        
        orderCreateDto.setOrderTicketUserCreateDtoList(orderTicketUserCreateDtoList);
        
        return orderCreateDto;
    }

    /**
     * 这是一个通过RPC调用订单服务创建订单的方法，包含异常处理和补偿机制。
     * RPC调用创建订单：调用订单微服务创建订单
     * 异常处理：处理订单创建失败的情况
     * 补偿机制：订单创建失败时回滚缓存状态
     * 日志记录：记录失败订单供人工处理
     * 异常传播：将错误信息传递给调用方
     * @param orderCreateDto
     * @param purchaseSeatList
     * @return
     */
    private String createOrderByRpc(OrderCreateDto orderCreateDto,List<SeatVo> purchaseSeatList){
        ApiResponse<String> createOrderResponse = orderClient.create(orderCreateDto);
        // 如果订单创建失败，进入异常处理
        if (!Objects.equals(createOrderResponse.getCode(), BaseCode.SUCCESS.getCode())) {
            log.error("创建订单失败 需人工处理 orderCreateDto : {}",JSON.toJSONString(orderCreateDto));
            // 如果订单创建失败，需要将座位状态恢复为未售
            updateProgramCacheDataResolution(orderCreateDto.getProgramId(),purchaseSeatList,OrderStatus.CANCEL);
            throw new DaMaiFrameException(createOrderResponse);
        }
        return createOrderResponse.getData();
    }
    
    private String createOrderByMq(OrderCreateDto orderCreateDto,List<SeatVo> purchaseSeatList){
        CreateOrderMqDomain createOrderMqDomain = new CreateOrderMqDomain();
        CountDownLatch latch = new CountDownLatch(1);
        createOrderSend.sendMessage(JSON.toJSONString(orderCreateDto),sendResult -> {
            createOrderMqDomain.orderNumber = String.valueOf(orderCreateDto.getOrderNumber());
            assert sendResult != null;
            log.info("创建订单kafka发送消息成功 topic : {}",sendResult.getRecordMetadata().topic());
            latch.countDown();
        },ex -> {
            log.error("创建订单kafka发送消息失败 error",ex);
            log.error("创建订单失败 需人工处理 orderCreateDto : {}",JSON.toJSONString(orderCreateDto));
            updateProgramCacheDataResolution(orderCreateDto.getProgramId(),purchaseSeatList,OrderStatus.CANCEL);
            createOrderMqDomain.daMaiFrameException = new DaMaiFrameException(ex);
            latch.countDown();
        });
        try {
            latch.await();
        } catch (InterruptedException e) {
            log.error("createOrderByMq InterruptedException",e);
            throw new DaMaiFrameException(e);
        }
        if (Objects.nonNull(createOrderMqDomain.daMaiFrameException)) {
            throw createOrderMqDomain.daMaiFrameException;
        }
        return createOrderMqDomain.orderNumber;
    }

    /**
     * 该方法本质上时一个面向lua脚本的拼接方法
     * 这是一个复杂的缓存更新方法，用于处理订单状态变化时的库存和座位缓存更新。
     * 订单状态驱动的缓存更新：根据订单状态（未支付/取消）更新库存和座位缓存
     * 库存余量更新：更新Redis中的库存余量
     * 座位状态更新：更新座位的销售状态（未售↔锁定）
     * 批量操作：支持批量座位更新
     * Lua脚本执行：将所有更新操作打包到Lua脚本中执行
     * @param programId 节目id
     * @param seatVoList  涉及更新的座位列表
     * @param orderStatus   订单状态
     */
    private void updateProgramCacheDataResolution(Long programId,List<SeatVo> seatVoList,OrderStatus orderStatus){
        //参数校验：用户创建订单但未支付；用户取消订单
        if (!(Objects.equals(orderStatus.getCode(), OrderStatus.NO_PAY.getCode()) ||
                Objects.equals(orderStatus.getCode(), OrderStatus.CANCEL.getCode()))) {
            throw new DaMaiFrameException(BaseCode.OPERATE_ORDER_STATUS_NOT_PERMIT);
        }
        List<String> keys = new ArrayList<>();
        keys.add("#");  // Lua脚本占位符
        
        String[] data = new String[3];
        //按票价分类统计座位数量
        Map<Long, Long> ticketCategoryCountMap =
                seatVoList.stream().collect(Collectors.groupingBy(SeatVo::getTicketCategoryId, Collectors.counting()));//（按票价分类ID分组，统计每个分类的座位数）
        //构建库存更新JSON
        /**
         * [
         *   {
         *     "programTicketRemainNumberHashKey": "program:ticket:remain:number:hash:resolution:1001:1",
         *     "ticketCategoryId": "1",
         *     "count": "-2"  // 减少2个库存
         *   }
         * ]
         */
        JSONArray jsonArray = new JSONArray();
        ticketCategoryCountMap.forEach((k,v) -> {
            // 库存Hash键
            JSONObject jsonObject = new JSONObject();
            jsonObject.put("programTicketRemainNumberHashKey",RedisKeyBuild.createRedisKey(
                    RedisKeyManage.PROGRAM_TICKET_REMAIN_NUMBER_HASH_RESOLUTION, programId, k).getRelKey());
            jsonObject.put("ticketCategoryId",String.valueOf(k));
            if (Objects.equals(orderStatus.getCode(), OrderStatus.NO_PAY.getCode())) {
                // 创建订单：库存减少
                jsonObject.put("count","-" + v);
            } else if (Objects.equals(orderStatus.getCode(), OrderStatus.CANCEL.getCode())) {
                // 取消订单：库存恢复
                jsonObject.put("count",v);
            }
            jsonArray.add(jsonObject);
        });
        //按票价分类分组座位
        Map<Long, List<SeatVo>> seatVoMap = 
                seatVoList.stream().collect(Collectors.groupingBy(SeatVo::getTicketCategoryId));
        JSONArray delSeatIdjsonArray = new JSONArray(); //构建座位删除JSON
        JSONArray addSeatDatajsonArray = new JSONArray(); //构建座位添加JSON
        seatVoMap.forEach((k,v) -> {
            JSONObject delSeatIdjsonObject = new JSONObject();
            JSONObject seatDatajsonObject = new JSONObject();
            String seatHashKeyDel = "";
            String seatHashKeyAdd = "";
            if (Objects.equals(orderStatus.getCode(), OrderStatus.NO_PAY.getCode())) {
                //没有售卖座位的key
                seatHashKeyDel = (RedisKeyBuild.createRedisKey(RedisKeyManage.PROGRAM_SEAT_NO_SOLD_RESOLUTION_HASH, programId, k).getRelKey());
                //锁定座位的key
                seatHashKeyAdd = (RedisKeyBuild.createRedisKey(RedisKeyManage.PROGRAM_SEAT_LOCK_RESOLUTION_HASH, programId, k).getRelKey());
                //改变座位状态信息为锁定状态
                for (SeatVo seatVo : v) {
                    seatVo.setSellStatus(SellStatus.LOCK.getCode());
                }
            } else if (Objects.equals(orderStatus.getCode(), OrderStatus.CANCEL.getCode())) {
                //锁定座位的key
                seatHashKeyDel = (RedisKeyBuild.createRedisKey(RedisKeyManage.PROGRAM_SEAT_LOCK_RESOLUTION_HASH, programId, k).getRelKey());
                //没有售卖座位的key
                seatHashKeyAdd = (RedisKeyBuild.createRedisKey(RedisKeyManage.PROGRAM_SEAT_NO_SOLD_RESOLUTION_HASH, programId, k).getRelKey());
                // 更新座位状态为未售
                for (SeatVo seatVo : v) {
                    seatVo.setSellStatus(SellStatus.NO_SOLD.getCode());
                }
            }
            //要进行删除座位的key
            delSeatIdjsonObject.put("seatHashKeyDel",seatHashKeyDel);
            //如果是订单创建，那么就扣除未售卖的座位id
            //如果是订单取消，那么就扣除锁定的座位id
            delSeatIdjsonObject.put("seatIdList",v.stream().map(SeatVo::getId).map(String::valueOf).collect(Collectors.toList()));
            delSeatIdjsonArray.add(delSeatIdjsonObject);
            //要进行添加座位的key
            seatDatajsonObject.put("seatHashKeyAdd",seatHashKeyAdd);
            List<String> seatDataList = new ArrayList<>();
            for (SeatVo seatVo : v) {
                seatDataList.add(String.valueOf(seatVo.getId()));
                seatDataList.add(JSON.toJSONString(seatVo));
            }
            seatDatajsonObject.put("seatDataList",seatDataList);
            addSeatDatajsonArray.add(seatDatajsonObject);
        });
        
        data[0] = JSON.toJSONString(jsonArray); // 库存更新数据
        data[1] = JSON.toJSONString(delSeatIdjsonArray); // 座位删除数据
        data[2] = JSON.toJSONString(addSeatDatajsonArray); // 座位添加数据
        //执行lua脚本
        programCacheResolutionOperate.programCacheOperate(keys,data);
    }
}
