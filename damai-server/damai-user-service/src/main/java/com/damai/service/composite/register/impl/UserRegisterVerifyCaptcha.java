package com.damai.service.composite.register.impl;

import com.damai.captcha.model.common.ResponseModel;
import com.damai.captcha.model.vo.CaptchaVO;
import com.damai.core.RedisKeyManage;
import com.damai.util.StringUtil;
import com.damai.dto.UserRegisterDto;
import com.damai.enums.BaseCode;
import com.damai.enums.VerifyCaptcha;
import com.damai.exception.DaMaiFrameException;
import com.damai.redis.RedisCache;
import com.damai.redis.RedisKeyBuild;
import com.damai.service.CaptchaHandle;
import com.damai.service.composite.register.AbstractUserRegisterCheckHandler;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;

/**
 * @program: 极度真实还原大麦网高并发实战项目。 添加 阿星不是程序员 微信，添加时备注 大麦 来获取项目的完整资料 
 * @description: 用户注册检查
 * @author: 阿星不是程序员
 * 开始
 *   ↓
 * 检查密码是否一致 → 否 → 抛出"两次密码不同"异常
 *   ↓是
 * 检查验证码ID是否存在 → 否 → 抛出"验证码ID不存在"异常
 *   ↓是
 * 检查是否需要验证码 → 否 → 验证通过
 *   ↓是
 * 检查验证码是否为空 → 是 → 抛出"验证码为空"异常
 *   ↓否
 * 调用验证码服务验证
 *   ↓
 * 验证是否成功？ → 否 → 抛出验证失败异常
 *   ↓是
 * 验证通过，继续下一个处理器
 **/
@Slf4j
@Component
public class UserRegisterVerifyCaptcha extends AbstractUserRegisterCheckHandler {
    
    @Autowired
    private CaptchaHandle captchaHandle;
    
    @Autowired
    private RedisCache redisCache;

    /**
     * 验证验证码是否正确
     * @param param 泛型参数，用于业务执行。
     */
    @Override
    protected void execute(UserRegisterDto param) {
        //验证密码
        String password = param.getPassword();
        String confirmPassword = param.getConfirmPassword();
        if (!password.equals(confirmPassword)) {
            throw new DaMaiFrameException(BaseCode.TWO_PASSWORDS_DIFFERENT);
        }
        //验证码ID有效性检查
        String verifyCaptcha = redisCache.get(RedisKeyBuild.createRedisKey(RedisKeyManage.VERIFY_CAPTCHA_ID,param.getCaptchaId()), String.class);
        if (StringUtil.isEmpty(verifyCaptcha)) {
            throw new DaMaiFrameException(BaseCode.VERIFY_CAPTCHA_ID_NOT_EXIST);
        }
        if (VerifyCaptcha.YES.getValue().equals(verifyCaptcha)) {
            //需要验证验证码
            if (StringUtil.isEmpty(param.getCaptchaVerification())) {
                throw new DaMaiFrameException(BaseCode.VERIFY_CAPTCHA_EMPTY);
            }
            log.info("传入的captchaVerification:{}",param.getCaptchaVerification());
            //构建验证请求
            CaptchaVO captchaVO = new CaptchaVO();
            captchaVO.setCaptchaVerification(param.getCaptchaVerification());
            //    // 调用验证服务
            ResponseModel responseModel = captchaHandle.verification(captchaVO);
            if (!responseModel.isSuccess()) {
                throw new DaMaiFrameException(responseModel.getRepCode(),responseModel.getRepMsg());
            }
        }
    }
    
    @Override
    public Integer executeParentOrder() {
        return 0;
    }
    
    @Override
    public Integer executeTier() {
        return 1;
    }
    
    @Override
    public Integer executeOrder() {
        return 1;
    }
}
