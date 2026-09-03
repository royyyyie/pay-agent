package com.damai.service.strategy.impl;

import com.damai.core.RepeatExecuteLimitConstants;
import com.damai.dto.ProgramOrderCreateDto;
import com.damai.enums.CompositeCheckType;
import com.damai.enums.ProgramOrderVersion;
import com.damai.initialize.impl.composite.CompositeContainer;
import com.damai.repeatexecutelimit.annotion.RepeatExecuteLimit;
import com.damai.service.ProgramOrderService;
import com.damai.service.strategy.ProgramOrderStrategy;
import com.damai.servicelock.annotion.ServiceLock;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;

import static com.damai.core.DistributedLockConstants.PROGRAM_ORDER_CREATE_V1;

/**
 * @program: 极度真实还原大麦网高并发实战项目。 添加 阿星不是程序员 微信，添加时备注 大麦 来获取项目的完整资料 
 * @description: 节目订单v1
 * @author: 阿星不是程序员
 **/
@Component
public class ProgramOrderV1Strategy implements ProgramOrderStrategy {
    
    @Autowired
    private ProgramOrderService programOrderService;
    
    @Autowired
    private CompositeContainer compositeContainer;

    /**
     * 订单创建，使用节目id作为锁
     * */

    //幂等性保护：为了防止用户多次提交，前端的解决办法是将按钮置灰，但这种不靠谱。如果前端没有控制住、网络延迟、或者有人刷号直接调用接口来多次请求就会出现问题了，所以说后端也要做好幂等保护
    @RepeatExecuteLimit(
            name = RepeatExecuteLimitConstants.CREATE_PROGRAM_ORDER,
            keys = {"#programOrderCreateDto.userId","#programOrderCreateDto.programId"})
    @ServiceLock(name = PROGRAM_ORDER_CREATE_V1,keys = {"#programOrderCreateDto.programId"})
    @Override
    public String createOrder(final ProgramOrderCreateDto programOrderCreateDto) {
        //进行业务验证：此验证是使用了组合模式和树形结构来将业务的验证逻辑进行复用并且串联起来按照树形结构执行
        compositeContainer.execute(CompositeCheckType.PROGRAM_ORDER_CREATE_CHECK.getValue(),programOrderCreateDto);
        return programOrderService.create(programOrderCreateDto);
    }
    
    @Override
    public String version() {
        return ProgramOrderVersion.V1_VERSION.getVersion();
    }
}
