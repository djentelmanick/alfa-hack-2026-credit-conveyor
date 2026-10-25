from decimal import Decimal

from scoring import (
    CreditHistoryStatus,
    Decision,
    ProductVariant,
    RiskClass,
    ScoringInput,
    calculate_score,
    make_decision,
)

NO_COLLATERAL = ProductVariant(
    min_amount=Decimal("300000"),
    max_amount=Decimal("30000000"),
    min_term_months=12,
    max_term_months=60,
    min_rate=Decimal("0.235"),
    max_rate=Decimal("0.295"),
    revenue_limit_pct=Decimal("0.30"),
)

WITH_COLLATERAL = ProductVariant(
    min_amount=Decimal("500000"),
    max_amount=Decimal("150000000"),
    min_term_months=12,
    max_term_months=120,
    min_rate=Decimal("0.215"),
    max_rate=Decimal("0.275"),
    revenue_limit_pct=Decimal("0.50"),
)


def _score_and_decide(inp: ScoringInput, **kwargs):
    result = calculate_score(inp)
    return result, make_decision(result, inp, **kwargs)


# ── Сценарий 1: Пекарня Колос — одобрено, A ──────────────────────


class TestScenario1Approved:
    def test_approved_with_min_rate(self):
        inp = ScoringInput(
            business_age_months=60,
            okved_code="56.10",
            is_mass_address=False,
            is_mass_director_or_founder=False,
            credit_history_status=CreditHistoryStatus.CLEAN,
            has_closed_credits_without_overdue=True,
            credit_inquiries_3_months=0,
            monthly_revenue=Decimal("2000000"),
            existing_monthly_payments=Decimal("50000"),
            requested_amount=Decimal("2000000"),
            requested_term_months=12,
            min_annual_rate=Decimal("0.235"),
        )
        score, decision = _score_and_decide(inp, variant=NO_COLLATERAL)
        assert score.risk_class == RiskClass.A
        assert decision.decision == Decision.APPROVED
        assert decision.rate == Decimal("0.235")


# ── Сценарий 3: ИП Соколов — контрпредложение, сумма > лимита ────


class TestScenario3OverLimit:
    def test_counter_offer_over_limit(self):
        inp = ScoringInput(
            business_age_months=48,
            okved_code="47.52",
            is_mass_address=False,
            is_mass_director_or_founder=False,
            credit_history_status=CreditHistoryStatus.CLEAN,
            has_closed_credits_without_overdue=False,
            credit_inquiries_3_months=0,
            monthly_revenue=Decimal("1000000"),
            existing_monthly_payments=Decimal("100000"),
            requested_amount=Decimal("4500000"),
            requested_term_months=24,
            min_annual_rate=Decimal("0.235"),
        )
        score, decision = _score_and_decide(
            inp, variant=NO_COLLATERAL, collateral_variant=WITH_COLLATERAL,
        )
        assert score.risk_class == RiskClass.B
        assert decision.decision == Decision.COUNTER_OFFER
        assert decision.options is not None
        assert len(decision.options) >= 1

        no_col = [o for o in decision.options if not o.with_collateral]
        assert len(no_col) == 1
        assert no_col[0].amount == Decimal("3600000")

    def test_collateral_option_available(self):
        inp = ScoringInput(
            business_age_months=48,
            okved_code="47.52",
            is_mass_address=False,
            is_mass_director_or_founder=False,
            credit_history_status=CreditHistoryStatus.CLEAN,
            has_closed_credits_without_overdue=False,
            credit_inquiries_3_months=0,
            monthly_revenue=Decimal("1000000"),
            existing_monthly_payments=Decimal("100000"),
            requested_amount=Decimal("4500000"),
            requested_term_months=24,
            min_annual_rate=Decimal("0.235"),
        )
        score, decision = _score_and_decide(
            inp, variant=NO_COLLATERAL, collateral_variant=WITH_COLLATERAL,
        )
        col = [o for o in decision.options if o.with_collateral]
        assert len(col) == 1
        assert col[0].amount == Decimal("4500000")


# ── Сценарий 5: Техпром — ручная проверка (залог + сумма > 5 млн) ─


class TestScenario5ManualReview:
    def test_manual_review_with_reasons(self):
        inp = ScoringInput(
            business_age_months=84,
            okved_code="25.62",
            is_mass_address=False,
            is_mass_director_or_founder=False,
            credit_history_status=CreditHistoryStatus.CLEAN,
            has_closed_credits_without_overdue=True,
            credit_inquiries_3_months=0,
            monthly_revenue=Decimal("5000000"),
            existing_monthly_payments=Decimal("400000"),
            requested_amount=Decimal("12000000"),
            requested_term_months=60,
            min_annual_rate=Decimal("0.23"),
        )
        reasons = ["Вариант с залогом", "Сумма больше 5 млн ₽"]
        score, decision = _score_and_decide(
            inp, variant=WITH_COLLATERAL, manual_review_reasons=reasons,
        )
        assert score.risk_class == RiskClass.A
        assert decision.decision == Decision.MANUAL_REVIEW
        assert decision.reasons == reasons


# ── Класс D — отказ ───────────────────────────────────────────────


class TestClassDRejected:
    def test_rejected_with_explanations(self):
        inp = ScoringInput(
            business_age_months=6,
            okved_code="41.20",
            is_mass_address=True,
            is_mass_director_or_founder=True,
            credit_history_status=CreditHistoryStatus.MAJOR_OVERDUE,
            has_closed_credits_without_overdue=False,
            credit_inquiries_3_months=5,
            monthly_revenue=Decimal("500000"),
            existing_monthly_payments=Decimal("300000"),
            requested_amount=Decimal("5000000"),
            requested_term_months=12,
            min_annual_rate=Decimal("0.295"),
            last_2q_revenue=Decimal("500000"),
            prev_2q_revenue=Decimal("1000000"),
        )
        score, decision = _score_and_decide(inp, variant=NO_COLLATERAL)
        assert score.risk_class == RiskClass.D
        assert decision.decision == Decision.REJECTED
        assert decision.reasons is not None
        assert len(decision.reasons) > 0
        # Антифрод-факторы не попадают в причины
        for reason in decision.reasons:
            assert "массов" not in reason.lower()
            assert "обращени" not in reason.lower()


# ── Класс C — контрпредложение с подбором условий для B ───────────


class TestClassCCounterOffer:
    def test_finds_lower_amount(self):
        # 20(age) + 10(debt 20-40%) + 5(ltr 25-50%) + 0(no history) = 35, C
        # При уменьшении суммы: debt→+25, ltr→+15 → 60, B
        inp = ScoringInput(
            business_age_months=48,
            okved_code="47.19",
            is_mass_address=False,
            is_mass_director_or_founder=False,
            credit_history_status=CreditHistoryStatus.NO_HISTORY,
            has_closed_credits_without_overdue=False,
            credit_inquiries_3_months=0,
            monthly_revenue=Decimal("1000000"),
            existing_monthly_payments=Decimal("100000"),
            requested_amount=Decimal("4000000"),
            requested_term_months=36,
            min_annual_rate=Decimal("0.235"),
        )
        score, decision = _score_and_decide(inp, variant=NO_COLLATERAL)
        assert score.risk_class == RiskClass.C
        assert decision.decision == Decision.COUNTER_OFFER

        for opt in decision.options:
            assert opt.risk_class == RiskClass.B
            assert opt.amount <= inp.requested_amount

    def test_fallback_to_analyst_when_impossible(self):
        # 10(age) + 25(debt <20%) + 5(ltr 25-50%) + 0(no history) -10(high) = 30, C
        # Даже при минимальной сумме max = 10+25+15+0-10 = 40, всё ещё C
        inp = ScoringInput(
            business_age_months=18,
            okved_code="41.20",
            is_mass_address=False,
            is_mass_director_or_founder=False,
            credit_history_status=CreditHistoryStatus.NO_HISTORY,
            has_closed_credits_without_overdue=False,
            credit_inquiries_3_months=0,
            monthly_revenue=Decimal("1000000"),
            existing_monthly_payments=Decimal("0"),
            requested_amount=Decimal("4000000"),
            requested_term_months=60,
            min_annual_rate=Decimal("0.235"),
        )
        score, decision = _score_and_decide(inp, variant=NO_COLLATERAL)
        assert score.risk_class == RiskClass.C
        assert decision.decision == Decision.MANUAL_REVIEW


# ── Ручная проверка приоритетнее скоринга ─────────────────────────


class TestManualReviewPriority:
    def test_manual_review_overrides_approval(self):
        inp = ScoringInput(
            business_age_months=60,
            okved_code="62.01",
            is_mass_address=False,
            is_mass_director_or_founder=False,
            credit_history_status=CreditHistoryStatus.CLEAN,
            has_closed_credits_without_overdue=True,
            credit_inquiries_3_months=0,
            monthly_revenue=Decimal("5000000"),
            existing_monthly_payments=Decimal("0"),
            requested_amount=Decimal("1000000"),
            requested_term_months=12,
            min_annual_rate=Decimal("0.235"),
        )
        score, decision = _score_and_decide(
            inp,
            variant=NO_COLLATERAL,
            manual_review_reasons=["Жёлтый уровень риска ЦБ"],
        )
        assert score.risk_class == RiskClass.A
        assert decision.decision == Decision.MANUAL_REVIEW
