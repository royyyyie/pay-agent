package com.damai.controller.agent.purchase;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import org.apache.ibatis.annotations.Insert;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;
import org.apache.ibatis.annotations.Update;

import java.util.Date;

@Mapper
public interface PurchaseIntentMapper extends BaseMapper<PurchaseIntent> {

    @Insert("""
            INSERT IGNORE INTO d_agent_purchase_intent (
                id, tenant_id, user_id, session_key, idempotency_key, program_id,
                ticket_category_id, quantity, unit_amount_fen, total_amount_fen,
                currency, quote_hash, quote_expires_at, intent_state, version,
                create_time, edit_time
            ) VALUES (
                #{id}, #{tenantId}, #{userId}, #{sessionKey}, #{idempotencyKey}, #{programId},
                #{ticketCategoryId}, #{quantity}, #{unitAmountFen}, #{totalAmountFen},
                #{currency}, #{quoteHash}, #{quoteExpiresAt}, #{intentState}, #{version},
                #{createTime}, #{editTime}
            )
            """)
    int insertIdempotent(PurchaseIntent intent);

    @Select("""
            SELECT * FROM d_agent_purchase_intent
            WHERE tenant_id = #{tenantId} AND user_id = #{userId}
              AND program_id = #{programId} AND idempotency_key = #{idempotencyKey}
            LIMIT 1
            """)
    PurchaseIntent selectByIdempotency(
            @Param("tenantId") String tenantId,
            @Param("userId") String userId,
            @Param("programId") Long programId,
            @Param("idempotencyKey") String idempotencyKey);

    @Select("""
            SELECT * FROM d_agent_purchase_intent
            WHERE id = #{intentId} AND program_id = #{programId}
              AND tenant_id = #{tenantId} AND user_id = #{userId}
            LIMIT 1
            """)
    PurchaseIntent selectOwned(
            @Param("tenantId") String tenantId,
            @Param("userId") String userId,
            @Param("intentId") Long intentId,
            @Param("programId") Long programId);

    @Update("""
            UPDATE d_agent_purchase_intent
            SET intent_state = 'CANCELLED', version = version + 1, edit_time = #{now}
            WHERE id = #{intentId} AND program_id = #{programId}
              AND tenant_id = #{tenantId} AND user_id = #{userId}
              AND version = #{expectedVersion} AND intent_state = 'PENDING_CONFIRMATION'
            """)
    int cancel(
            @Param("tenantId") String tenantId,
            @Param("userId") String userId,
            @Param("intentId") Long intentId,
            @Param("programId") Long programId,
            @Param("expectedVersion") Long expectedVersion,
            @Param("now") Date now);

    @Update("""
            UPDATE d_agent_purchase_intent
            SET intent_state = 'CONFIRMED', confirmed_at = #{confirmedAt},
                version = version + 1, edit_time = #{confirmedAt}
            WHERE id = #{intentId} AND program_id = #{programId}
              AND tenant_id = #{tenantId} AND user_id = #{userId}
              AND session_key = #{sessionKey} AND version = #{expectedVersion}
              AND quote_hash = #{quoteHash} AND intent_state = 'PENDING_CONFIRMATION'
              AND quote_expires_at > #{confirmedAt}
            """)
    int confirm(
            @Param("tenantId") String tenantId,
            @Param("userId") String userId,
            @Param("sessionKey") String sessionKey,
            @Param("intentId") Long intentId,
            @Param("programId") Long programId,
            @Param("expectedVersion") Long expectedVersion,
            @Param("quoteHash") String quoteHash,
            @Param("confirmedAt") Date confirmedAt);
}
