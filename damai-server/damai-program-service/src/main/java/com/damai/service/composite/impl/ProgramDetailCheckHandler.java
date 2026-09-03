package com.damai.service.composite.impl;


import com.damai.dto.ProgramGetDto;
import com.damai.dto.ProgramOrderCreateDto;
import com.damai.enums.BaseCode;
import com.damai.enums.BusinessStatus;
import com.damai.exception.DaMaiFrameException;
import com.damai.service.ProgramService;
import com.damai.service.composite.AbstractProgramCheckHandler;
import com.damai.vo.ProgramVo;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;

import java.util.List;
import java.util.Objects;
import java.util.Optional;

/**
 * @program: 极度真实还原大麦网高并发实战项目。 添加 阿星不是程序员 微信，添加时备注 大麦 来获取项目的完整资料 
 * @description: 节目检查
 * @author: 阿星不是程序员
 **/
@Component
public class ProgramDetailCheckHandler extends AbstractProgramCheckHandler {
    
    @Autowired
    private ProgramService programService;
    
    @Override
    protected void execute(final ProgramOrderCreateDto programOrderCreateDto) {
        //查询要购买的节目
        ProgramGetDto programGetDto = new ProgramGetDto();
        programGetDto.setId(programOrderCreateDto.getProgramId());
        ProgramVo programVo = programService.detailV2(programGetDto);
        //如果节目不允许选择座位，但传入的了手动座位，则抛出异常
        if (programVo.getPermitChooseSeat().equals(BusinessStatus.NO.getCode())) {
            if (Objects.nonNull(programOrderCreateDto.getSeatDtoList())) {
                throw new DaMaiFrameException(BaseCode.PROGRAM_NOT_ALLOW_CHOOSE_SEAT);
            }
        }
        //手动选择座位时，选择座位的数量
        Integer seatCount = Optional.ofNullable(programOrderCreateDto.getSeatDtoList()).map(List::size).orElse(0);
        //自动匹配座位时，选择票档的数量
        Integer ticketCount = Optional.ofNullable(programOrderCreateDto.getTicketCount()).orElse(0);
        //只要有一个超过了规定的数量，那么就直接拒绝
        if (seatCount > programVo.getPerOrderLimitPurchaseCount() || ticketCount > programVo.getPerOrderLimitPurchaseCount()) {
            throw new DaMaiFrameException(BaseCode.PER_ORDER_PURCHASE_COUNT_OVER_LIMIT);
        }
    }
    
    @Override
    public Integer executeParentOrder() {
        return 1;
    }
    
    @Override
    public Integer executeTier() {
        return 2;
    }
    
    @Override
    public Integer executeOrder() {
        return 1;
    }
}
