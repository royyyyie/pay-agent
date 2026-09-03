package com.damai.service;

import com.damai.captcha.model.common.ResponseModel;
import com.damai.captcha.model.vo.CaptchaVO;
import com.baidu.fsg.uid.UidGenerator;
import com.damai.core.RedisKeyManage;
import com.damai.redis.RedisKeyBuild;
import com.damai.service.lua.CheckNeedCaptchaOperate;
import com.damai.vo.CheckNeedCaptchaDataVo;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.List;

/**
 * @program: 极度真实还原大麦网高并发实战项目。 添加 阿星不是程序员 微信，添加时备注 大麦 来获取项目的完整资料 
 * @description: 判断是否需要验证码
 * @author: 阿星不是程序员
 **/
@Service
public class UserCaptchaService {
    /**
     * 当每秒的注册请求达到阈值发校验码验证码的操作
     */
    @Value("${verify_captcha_threshold:10}")
    private int verifyCaptchaThreshold;
    /**
     * 验证验证码id的过期时间
     */
    @Value("${verify_captcha_id_expire_time:60}")
    private int verifyCaptchaIdExpireTime;
    /**
     * 始终进行校验验证码1：校验，0:不校验
     */
    @Value("${always_verify_captcha:0}")
    private int alwaysVerifyCaptcha;
    
    @Autowired
    private CaptchaHandle captchaHandle;
    
    @Autowired
    private UidGenerator uidGenerator;
    
    @Autowired
    private CheckNeedCaptchaOperate checkNeedCaptchaOperate;
    
    public CheckNeedCaptchaDataVo checkNeedCaptcha() {
        //当前时间戳
        long currentTimeMillis = System.currentTimeMillis();
        //分布式id生成唯一标识的id
        long id = uidGenerator.getUid();
        List<String> keys = new ArrayList<>();
        //计数器的键
        keys.add(RedisKeyBuild.createRedisKey(RedisKeyManage.COUNTER_COUNT).getRelKey());
        //计数器的时间戳的键
        keys.add(RedisKeyBuild.createRedisKey(RedisKeyManage.COUNTER_TIMESTAMP).getRelKey());
        //校验验证码唯一标识id的键
        keys.add(RedisKeyBuild.createRedisKey(RedisKeyManage.VERIFY_CAPTCHA_ID,id).getRelKey());
        String[] data = new String[4];
        //每秒的注册请求的阈值
        data[0] = String.valueOf(verifyCaptchaThreshold);
        //时间戳
        data[1] = String.valueOf(currentTimeMillis);
        //校验验证码id的过期时间
        data[2] = String.valueOf(verifyCaptchaIdExpireTime);
        //设置是否需要验证码
        data[3] = String.valueOf(alwaysVerifyCaptcha);
        //执行计数器计算
        Boolean result = checkNeedCaptchaOperate.checkNeedCaptchaOperate(keys, data);
        CheckNeedCaptchaDataVo checkNeedCaptchaDataVo = new CheckNeedCaptchaDataVo();
        checkNeedCaptchaDataVo.setCaptchaId(id);
        checkNeedCaptchaDataVo.setVerifyCaptcha(result);
        return checkNeedCaptchaDataVo;
    }
    
    public ResponseModel getCaptcha(CaptchaVO captchaVO) {
        return captchaHandle.getCaptcha(captchaVO);
    }
    
    public ResponseModel verifyCaptcha(final CaptchaVO captchaVO) {
        return captchaHandle.checkCaptcha(captchaVO);
    }
}
