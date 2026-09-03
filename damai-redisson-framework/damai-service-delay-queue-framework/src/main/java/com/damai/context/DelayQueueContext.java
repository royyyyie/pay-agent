package com.damai.context;

import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.TimeUnit;

/**
 * @program: 极度真实还原大麦网高并发实战项目。 添加 阿星不是程序员 微信，添加时备注 大麦 来获取项目的完整资料 
 * @description: 延迟队列 发送者上下文
 * @author: 阿星不是程序员
 **/
public class DelayQueueContext {
    
    private final DelayQueueBasePart delayQueueBasePart;
    /**
     * key为topic主题，value为发送消息的处理器
     * */
    private final Map<String, DelayQueueProduceCombine> delayQueueProduceCombineMap = new ConcurrentHashMap<>();
    
    public DelayQueueContext(DelayQueueBasePart delayQueueBasePart){
        this.delayQueueBasePart = delayQueueBasePart;
    }


    /**
     * 这是一个延时队列的上下文管理器，用于发送延时消息。
     * 延时消息发送：支持发送延时执行的消息
     * 主题管理：按主题管理不同的延时队列处理器
     * 延迟处理：消息在指定延迟时间后才会被消费
     * 线程安全：使用ConcurrentHashMap保证并发安全
     * @param topic
     * @param content
     * @param delayTime
     * @param timeUnit
     */
    public void sendMessage(String topic,String content,long delayTime, TimeUnit timeUnit) {
        DelayQueueProduceCombine delayQueueProduceCombine = delayQueueProduceCombineMap.computeIfAbsent(
                topic, k -> new DelayQueueProduceCombine(delayQueueBasePart,topic));
        delayQueueProduceCombine.offer(content,delayTime,timeUnit);
    }
}
