package com.damai.controller.agent.purchase;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import org.apache.ibatis.annotations.Insert;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;
import org.apache.ibatis.annotations.Update;

import java.util.Date;
import java.util.List;

@Mapper
public interface PurchaseOrderSubmissionMapper extends BaseMapper<PurchaseOrderSubmission> {

    @Insert("""
            INSERT INTO d_agent_order_submission (
                id, intent_id, program_id, intent_version, tenant_id, user_id, session_key,
                ticket_category_id, quantity, ticket_user_refs, unit_amount_fen,
                total_amount_fen, currency, order_number, submission_state, attempt_count,
                next_attempt_time, create_time, edit_time
            ) VALUES (
                #{id}, #{intentId}, #{programId}, #{intentVersion}, #{tenantId}, #{userId},
                #{sessionKey}, #{ticketCategoryId}, #{quantity}, #{ticketUserRefs},
                #{unitAmountFen}, #{totalAmountFen}, #{currency}, #{orderNumber},
                #{submissionState}, #{attemptCount}, #{nextAttemptTime}, #{createTime}, #{editTime}
            )
            """)
    int insertSubmission(PurchaseOrderSubmission submission);

    @Select("""
            SELECT * FROM d_agent_order_submission
            WHERE intent_id = #{intentId} AND program_id = #{programId}
            LIMIT 1
            """)
    PurchaseOrderSubmission selectByIntent(
            @Param("intentId") Long intentId, @Param("programId") Long programId);

    @Select("""
            SELECT * FROM d_agent_order_submission
            WHERE intent_id = #{intentId} AND program_id = #{programId}
              AND tenant_id = #{tenantId} AND user_id = #{userId}
            LIMIT 1
            """)
    PurchaseOrderSubmission selectOwned(
            @Param("tenantId") String tenantId,
            @Param("userId") String userId,
            @Param("intentId") Long intentId,
            @Param("programId") Long programId);

    @Select("""
            SELECT * FROM d_agent_order_submission
            WHERE submission_state IN ('PENDING', 'RETRY', 'RECONCILE', 'PROCESSING')
              AND next_attempt_time <= #{now}
              AND (lease_expires_at IS NULL OR lease_expires_at <= #{now})
              AND MOD(program_id, #{partitionCount}) = #{partition}
            ORDER BY next_attempt_time, id
            LIMIT #{limit}
            """)
    List<PurchaseOrderSubmission> selectDueCandidates(
            @Param("now") Date now,
            @Param("partition") int partition,
            @Param("partitionCount") int partitionCount,
            @Param("limit") int limit);

    @Update("""
            UPDATE d_agent_order_submission
            SET submission_state = 'PROCESSING', attempt_count = attempt_count + 1,
                lease_owner = #{leaseOwner}, lease_token = #{leaseToken},
                lease_expires_at = #{leaseExpiresAt}, edit_time = #{now}
            WHERE id = #{id} AND program_id = #{programId}
              AND submission_state IN ('PENDING', 'RETRY', 'RECONCILE', 'PROCESSING')
              AND next_attempt_time <= #{now}
              AND (lease_expires_at IS NULL OR lease_expires_at <= #{now})
            """)
    int tryClaim(
            @Param("id") Long id,
            @Param("programId") Long programId,
            @Param("now") Date now,
            @Param("leaseOwner") String leaseOwner,
            @Param("leaseToken") String leaseToken,
            @Param("leaseExpiresAt") Date leaseExpiresAt);

    @Update("""
            UPDATE d_agent_order_submission
            SET submission_state = 'SUBMITTED', submitted_at = #{submittedAt},
                last_error_code = NULL, lease_owner = NULL, lease_token = NULL,
                lease_expires_at = NULL, edit_time = #{submittedAt}
            WHERE id = #{id} AND program_id = #{programId}
              AND submission_state = 'PROCESSING'
              AND lease_owner = #{leaseOwner} AND lease_token = #{leaseToken}
            """)
    int markSubmitted(
            @Param("id") Long id,
            @Param("programId") Long programId,
            @Param("leaseOwner") String leaseOwner,
            @Param("leaseToken") String leaseToken,
            @Param("submittedAt") Date submittedAt);

    @Update("""
            UPDATE d_agent_order_submission
            SET submission_state = 'RETRY', next_attempt_time = #{nextAttemptTime},
                last_error_code = #{errorCode}, lease_owner = NULL, lease_token = NULL,
                lease_expires_at = NULL, edit_time = #{failedAt}
            WHERE id = #{id} AND program_id = #{programId}
              AND submission_state = 'PROCESSING'
              AND lease_owner = #{leaseOwner} AND lease_token = #{leaseToken}
            """)
    int markRetry(
            @Param("id") Long id,
            @Param("programId") Long programId,
            @Param("leaseOwner") String leaseOwner,
            @Param("leaseToken") String leaseToken,
            @Param("nextAttemptTime") Date nextAttemptTime,
            @Param("errorCode") String errorCode,
            @Param("failedAt") Date failedAt);

    @Update("""
            UPDATE d_agent_order_submission
            SET submission_state = 'RECONCILE', next_attempt_time = #{nextAttemptTime},
                last_error_code = #{errorCode}, lease_owner = NULL, lease_token = NULL,
                lease_expires_at = NULL, edit_time = #{failedAt}
            WHERE id = #{id} AND program_id = #{programId}
              AND submission_state = 'PROCESSING'
              AND lease_owner = #{leaseOwner} AND lease_token = #{leaseToken}
            """)
    int markReconcile(
            @Param("id") Long id,
            @Param("programId") Long programId,
            @Param("leaseOwner") String leaseOwner,
            @Param("leaseToken") String leaseToken,
            @Param("nextAttemptTime") Date nextAttemptTime,
            @Param("errorCode") String errorCode,
            @Param("failedAt") Date failedAt);

    @Update("""
            UPDATE d_agent_order_submission
            SET submission_state = #{nextState}, last_error_code = #{errorCode},
                lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL,
                edit_time = #{failedAt}
            WHERE id = #{id} AND program_id = #{programId}
              AND submission_state = 'PROCESSING'
              AND lease_owner = #{leaseOwner} AND lease_token = #{leaseToken}
            """)
    int markTerminal(
            @Param("id") Long id,
            @Param("programId") Long programId,
            @Param("leaseOwner") String leaseOwner,
            @Param("leaseToken") String leaseToken,
            @Param("nextState") String nextState,
            @Param("errorCode") String errorCode,
            @Param("failedAt") Date failedAt);

    @Select("""
            SELECT COUNT(*) FROM d_agent_order_submission
            WHERE submission_state IN ('PENDING', 'RETRY', 'RECONCILE', 'PROCESSING')
              AND next_attempt_time <= #{now}
              AND (lease_expires_at IS NULL OR lease_expires_at <= #{now})
            """)
    long countDue(@Param("now") Date now);
}
