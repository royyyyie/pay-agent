package com.damai.controller.agent.purchase;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import org.apache.ibatis.annotations.Insert;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;

@Mapper
public interface ConfirmationGrantMapper extends BaseMapper<ConfirmationGrant> {

    @Insert("""
            INSERT INTO d_agent_confirmation_grant (
                id, intent_id, program_id, intent_version, tenant_id, user_id,
                session_key, quote_hash, proof_hash, nonce_hash, grant_state, expires_at,
                create_time, edit_time
            ) VALUES (
                #{id}, #{intentId}, #{programId}, #{intentVersion}, #{tenantId}, #{userId},
                #{sessionKey}, #{quoteHash}, #{proofHash}, #{nonceHash}, #{grantState}, #{expiresAt},
                #{createTime}, #{editTime}
            )
            """)
    int insertGrant(ConfirmationGrant grant);

    @Select("""
            SELECT * FROM d_agent_confirmation_grant
            WHERE intent_id = #{intentId} AND program_id = #{programId}
              AND proof_hash = #{proofHash}
            LIMIT 1
            """)
    ConfirmationGrant selectByProof(
            @Param("intentId") Long intentId,
            @Param("programId") Long programId,
            @Param("proofHash") String proofHash);

    @Select("""
            SELECT * FROM d_agent_confirmation_grant
            WHERE program_id = #{programId} AND nonce_hash = #{nonceHash}
            LIMIT 1
            """)
    ConfirmationGrant selectByNonce(
            @Param("programId") Long programId,
            @Param("nonceHash") String nonceHash);
}
