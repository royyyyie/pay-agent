package com.damai.controller.agent.purchase;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;

import java.util.Date;

/** Server-side one-time authority. No bearer token is exposed to the model. */
@Data
@TableName("d_agent_confirmation_grant")
public class ConfirmationGrant {

    @TableId(value = "id", type = IdType.INPUT)
    private Long id;
    private Long intentId;
    private Long programId;
    private Long intentVersion;
    private String tenantId;
    private String userId;
    private String sessionKey;
    private String quoteHash;
    private String proofHash;
    private String nonceHash;
    private String grantState;
    private Date expiresAt;
    private Date consumedAt;
    private Date createTime;
    private Date editTime;
}
