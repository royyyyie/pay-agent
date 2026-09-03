package com.damai.service;

import cn.hutool.core.bean.BeanUtil;
import com.alibaba.fastjson.JSON;
import com.baidu.fsg.uid.UidGenerator;
import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.core.conditions.update.LambdaUpdateWrapper;
import com.baomidou.mybatisplus.core.toolkit.Wrappers;
import com.damai.dto.NotifyDto;
import com.damai.dto.PayBillDto;
import com.damai.dto.PayDto;
import com.damai.dto.RefundDto;
import com.damai.dto.TradeCheckDto;
import com.damai.entity.PayBill;
import com.damai.entity.RefundBill;
import com.damai.enums.BaseCode;
import com.damai.enums.PayBillStatus;
import com.damai.exception.DaMaiFrameException;
import com.damai.mapper.PayBillMapper;
import com.damai.mapper.RefundBillMapper;
import com.damai.pay.PayResult;
import com.damai.pay.PayStrategyContext;
import com.damai.pay.PayStrategyHandler;
import com.damai.pay.RefundResult;
import com.damai.pay.TradeResult;
import com.damai.servicelock.annotion.ServiceLock;
import com.damai.util.DateUtils;
import com.damai.vo.NotifyVo;
import com.damai.vo.PayBillVo;
import com.damai.vo.TradeCheckVo;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.util.Map;
import java.util.Objects;

import static com.damai.constant.Constant.ALIPAY_NOTIFY_FAILURE_RESULT;
import static com.damai.constant.Constant.ALIPAY_NOTIFY_SUCCESS_RESULT;
import static com.damai.core.DistributedLockConstants.COMMON_PAY;
import static com.damai.core.DistributedLockConstants.TRADE_CHECK;

/**
 * @program: 极度真实还原大麦网高并发实战项目。 添加 阿星不是程序员 微信，添加时备注 大麦 来获取项目的完整资料 
 * @description: 支付 service
 * @author: 阿星不是程序员
 **/
@Slf4j
@Service
public class PayService {
    
    @Autowired
    private PayBillMapper payBillMapper;
    
    @Autowired
    private RefundBillMapper refundBillMapper;
    
    @Autowired
    private PayStrategyContext payStrategyContext;
    
    @Autowired
    private UidGenerator uidGenerator;
    
    /**
     * 通用支付，用订单号加锁防止多次支付成功，不依赖第三方支付的幂等性
     * 创建或更新支付单
     * 防止并发支付
     * 保证支付数据的一致性
     * */
    @ServiceLock(name = COMMON_PAY,keys = {"#payDto.orderNumber"})
    @Transactional(rollbackFor = Exception.class)
    public String commonPay(PayDto payDto) {
        //查询支付单
        LambdaQueryWrapper<PayBill> payBillLambdaQueryWrapper =
                Wrappers.lambdaQuery(PayBill.class).eq(PayBill::getOutOrderNo, payDto.getOrderNumber());
        PayBill payBill = payBillMapper.selectOne(payBillLambdaQueryWrapper);
        //支付单状态校验
        if (Objects.nonNull(payBill) && !Objects.equals(payBill.getPayBillStatus(), PayBillStatus.NO_PAY.getCode())) {
            throw new DaMaiFrameException(BaseCode.PAY_BILL_IS_NOT_NO_PAY);
        }
        //选择支付策略
        PayStrategyHandler payStrategyHandler = payStrategyContext.get(payDto.getChannel());
        //调用支付接口
        PayResult pay = payStrategyHandler.pay(String.valueOf(payDto.getOrderNumber()), payDto.getPrice(),
                payDto.getSubject(),payDto.getNotifyUrl(),payDto.getReturnUrl());
        if (pay.isSuccess()) {
            // 不存在 创建支付单
            if (Objects.isNull(payBill)){
                payBill = new PayBill();
                payBill.setId(uidGenerator.getUid());
                payBill.setOutOrderNo(String.valueOf(payDto.getOrderNumber()));
                payBill.setPayChannel(payDto.getChannel());
                payBill.setPayScene("生产");
                payBill.setSubject(payDto.getSubject());
                payBill.setPayAmount(payDto.getPrice());
                payBill.setPayBillType(payDto.getPayBillType());
                payBill.setPayBillStatus(PayBillStatus.NO_PAY.getCode());
                payBill.setPayTime(DateUtils.now());
                payBillMapper.insert(payBill);
            }else {
                //已存在 更新
                PayBill updatePayBill = new PayBill();
                updatePayBill.setId(payBill.getId());
                updatePayBill.setPayTime(DateUtils.now());
                payBillMapper.updateById(updatePayBill);
            }
        }
        return pay.getBody();
    }

    /**
     * 支付回调通知的验证和处理方法
     * 签名验证：验证回调请求的签名合法性
     * 支付单查询：根据订单号查询支付单
     * 状态幂等性检查：避免重复处理已完成的支付单
     * 数据验证：验证回调数据与支付单数据的一致性
     * 状态更新：更新支付单状态为已支付
     * 事务管理：保证数据库操作的原子性
     * @param notifyDto
     * @return
     */
    @Transactional(rollbackFor = Exception.class)
    public NotifyVo notify(NotifyDto notifyDto){
        NotifyVo notifyVo = new NotifyVo();
        log.info("回调通知参数 ===> {}", JSON.toJSONString(notifyDto));
        Map<String, String> params = notifyDto.getParams();
        //获取支付策略渠道
        PayStrategyHandler payStrategyHandler = payStrategyContext.get(notifyDto.getChannel());
        //SDK验证签名
        boolean signVerifyResult = payStrategyHandler.signVerify(params);
        if (!signVerifyResult) {
            notifyVo.setPayResult(ALIPAY_NOTIFY_FAILURE_RESULT);
            return notifyVo;
        }
        //查询支付单
        LambdaQueryWrapper<PayBill> payBillLambdaQueryWrapper =
                Wrappers.lambdaQuery(PayBill.class).eq(PayBill::getOutOrderNo, params.get("out_trade_no"));
        PayBill payBill = payBillMapper.selectOne(payBillLambdaQueryWrapper);
        //幂等性检查
        if (Objects.isNull(payBill)) {
            log.error("账单为空 notifyDto : {}",JSON.toJSONString(notifyDto));
            notifyVo.setPayResult(ALIPAY_NOTIFY_FAILURE_RESULT);
            return notifyVo;
        }
        if (Objects.equals(payBill.getPayBillStatus(), PayBillStatus.PAY.getCode())) {
            log.info("账单已支付 notifyDto : {}",JSON.toJSONString(notifyDto));
            notifyVo.setOutTradeNo(payBill.getOutOrderNo());
            notifyVo.setPayResult(ALIPAY_NOTIFY_SUCCESS_RESULT);
            return notifyVo;
        }
        if (Objects.equals(payBill.getPayBillStatus(), PayBillStatus.CANCEL.getCode())) {
            log.info("账单已取消 notifyDto : {}",JSON.toJSONString(notifyDto));
            notifyVo.setOutTradeNo(payBill.getOutOrderNo());
            notifyVo.setPayResult(ALIPAY_NOTIFY_SUCCESS_RESULT);
            return notifyVo;
        }
        if (Objects.equals(payBill.getPayBillStatus(), PayBillStatus.REFUND.getCode())) {
            log.info("账单已退单 notifyDto : {}",JSON.toJSONString(notifyDto));
            notifyVo.setOutTradeNo(payBill.getOutOrderNo());
            notifyVo.setPayResult(ALIPAY_NOTIFY_SUCCESS_RESULT);
            return notifyVo;
        }
        //数据验证
        boolean dataVerify = payStrategyHandler.dataVerify(notifyDto.getParams(), payBill);
        if (!dataVerify) {
            notifyVo.setPayResult(ALIPAY_NOTIFY_FAILURE_RESULT);
            return notifyVo;
        }
        //更新账单为支付状态
        PayBill updatePayBill = new PayBill();
        updatePayBill.setPayBillStatus(PayBillStatus.PAY.getCode());
        LambdaUpdateWrapper<PayBill> payBillLambdaUpdateWrapper =
                Wrappers.lambdaUpdate(PayBill.class).eq(PayBill::getOutOrderNo, params.get("out_trade_no"));
        payBillMapper.update(updatePayBill,payBillLambdaUpdateWrapper);
        notifyVo.setOutTradeNo(payBill.getOutOrderNo());
        notifyVo.setPayResult(ALIPAY_NOTIFY_SUCCESS_RESULT);
        return notifyVo;
    }

    /**
     * 支付服务订单查询
     * @param tradeCheckDto
     * @return
     */
    @Transactional(rollbackFor = Exception.class)
    @ServiceLock(name = TRADE_CHECK,keys = {"#tradeCheckDto.outTradeNo"})
    public TradeCheckVo tradeCheck(TradeCheckDto tradeCheckDto) {
        TradeCheckVo tradeCheckVo = new TradeCheckVo();
        //通过渠道获取具体的支付渠道策略
        PayStrategyHandler payStrategyHandler = payStrategyContext.get(tradeCheckDto.getChannel());
        //调用支付状态查询
        TradeResult tradeResult = payStrategyHandler.queryTrade(tradeCheckDto.getOutTradeNo());
        BeanUtil.copyProperties(tradeResult,tradeCheckVo);
        if (!tradeResult.isSuccess()) {
            return tradeCheckVo;
        }
        BigDecimal totalAmount = tradeResult.getTotalAmount();
        String outTradeNo = tradeResult.getOutTradeNo();
        Integer payBillStatus = tradeResult.getPayBillStatus();
        //查询对应的账单信息
        LambdaQueryWrapper<PayBill> payBillLambdaQueryWrapper =
                Wrappers.lambdaQuery(PayBill.class).eq(PayBill::getOutOrderNo, outTradeNo);
        PayBill payBill = payBillMapper.selectOne(payBillLambdaQueryWrapper);
        if (Objects.isNull(payBill)) {
            log.error("账单为空 tradeCheckDto : {}",JSON.toJSONString(tradeCheckDto));
            return tradeCheckVo;
        }
        //如果支付渠道的金额和账单的金额不一致，则直接返回
        if (payBill.getPayAmount().compareTo(totalAmount) != 0) {
            log.error("支付渠道 和库中账单支付金额不一致 支付渠道支付金额 : {}, 库中账单支付金额 : {}, tradeCheckDto : {}",
                    totalAmount,payBill.getPayAmount(),JSON.toJSONString(tradeCheckDto));
            return tradeCheckVo;
        }
        //如果支付渠道的状态和账单的状态不一致，说明回调没有执行成功，则更新账单状态
        if (!Objects.equals(payBill.getPayBillStatus(), payBillStatus)) {
            log.warn("支付渠道和库中账单交易状态不一致 支付渠道payBillStatus : {}, 库中payBillStatus : {}, tradeCheckDto : {}",
                    payBillStatus,payBill.getPayBillStatus(),JSON.toJSONString(tradeCheckDto));
            PayBill updatePayBill = new PayBill();
            updatePayBill.setId(payBill.getId());
            updatePayBill.setPayBillStatus(payBillStatus);
            LambdaUpdateWrapper<PayBill> payBillLambdaUpdateWrapper =
                    Wrappers.lambdaUpdate(PayBill.class).eq(PayBill::getOutOrderNo, outTradeNo);
            payBillMapper.update(updatePayBill,payBillLambdaUpdateWrapper);
            return tradeCheckVo;
        }
        return tradeCheckVo;
    }
    
    @Transactional(rollbackFor = Exception.class)
    public String refund(RefundDto refundDto) {
        PayBill payBill = payBillMapper.selectOne(Wrappers.lambdaQuery(PayBill.class)
                .eq(PayBill::getOutOrderNo, refundDto.getOrderNumber()));
        if (Objects.isNull(payBill)) {
            throw new DaMaiFrameException(BaseCode.PAY_BILL_NOT_EXIST);
        }
        
        if (!Objects.equals(payBill.getPayBillStatus(), PayBillStatus.PAY.getCode())) {
            throw new DaMaiFrameException(BaseCode.PAY_BILL_IS_NOT_PAY_STATUS);
        }
        
        if (refundDto.getAmount().compareTo(payBill.getPayAmount()) > 0) {
            throw new DaMaiFrameException(BaseCode.REFUND_AMOUNT_GREATER_THAN_PAY_AMOUNT);
        }
        
        PayStrategyHandler payStrategyHandler = payStrategyContext.get(refundDto.getChannel());
        RefundResult refundResult = 
                payStrategyHandler.refund(refundDto.getOrderNumber(), refundDto.getAmount(), refundDto.getReason());
        if (refundResult.isSuccess()) {
            PayBill updatePayBill = new PayBill();
            updatePayBill.setId(payBill.getId());
            updatePayBill.setPayBillStatus(PayBillStatus.REFUND.getCode());
            payBillMapper.updateById(updatePayBill);
            RefundBill refundBill = new RefundBill();
            refundBill.setId(uidGenerator.getUid());
            refundBill.setOutOrderNo(payBill.getOutOrderNo());
            refundBill.setPayBillId(payBill.getId());
            refundBill.setRefundAmount(refundDto.getAmount());
            refundBill.setRefundStatus(2);
            refundBill.setRefundTime(DateUtils.now());
            refundBill.setReason(refundDto.getReason());
            refundBillMapper.insert(refundBill);
            return refundBill.getOutOrderNo();
        }else {
            throw new DaMaiFrameException(refundResult.getMessage());
        }
    }
    
    public PayBillVo detail(PayBillDto payBillDto) {
        PayBillVo payBillVo = new PayBillVo();
        LambdaQueryWrapper<PayBill> payBillLambdaQueryWrapper =
                Wrappers.lambdaQuery(PayBill.class).eq(PayBill::getOutOrderNo, payBillDto.getOrderNumber());
        PayBill payBill = payBillMapper.selectOne(payBillLambdaQueryWrapper);
        if (Objects.nonNull(payBill)) {
            BeanUtil.copyProperties(payBill,payBillVo);
        }
        return payBillVo;
    }
}
