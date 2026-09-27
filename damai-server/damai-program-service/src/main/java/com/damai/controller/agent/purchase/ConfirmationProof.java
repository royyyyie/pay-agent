package com.damai.controller.agent.purchase;

import java.util.Date;

public record ConfirmationProof(String proofHash, String nonceHash, Date confirmedAt) {
}
