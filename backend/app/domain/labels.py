"""Русские подписи перечислений для текстов проверок: их читают аналитик и клиент."""

from collections.abc import Mapping

from app.domain.enums import (
    ApplicationStatus,
    CompanyStatus,
    ConsentType,
    KycLevel,
    SignatureStatus,
)

APPLICATION_STATUS_LABELS: Mapping[ApplicationStatus, str] = {
    ApplicationStatus.DRAFT: "Черновик",
    ApplicationStatus.SUBMITTED: "Отправлена",
    ApplicationStatus.CHECKING: "Проверка",
    ApplicationStatus.NEEDS_INFO: "Нужны данные",
    ApplicationStatus.SCORING: "Скоринг",
    ApplicationStatus.MANUAL_REVIEW: "На рассмотрении",
    ApplicationStatus.APPROVED: "Одобрена",
    ApplicationStatus.COUNTER_OFFER: "Контрпредложение",
    ApplicationStatus.SIGNING: "Подписание",
    ApplicationStatus.ISSUED: "Выдан",
    ApplicationStatus.REJECTED: "Отказ",
    ApplicationStatus.CANCELLED: "Отменена",
}

COMPANY_STATUS_LABELS: Mapping[CompanyStatus, str] = {
    CompanyStatus.ACTIVE: "действует",
    CompanyStatus.LIQUIDATED: "ликвидирована",
    CompanyStatus.BANKRUPT: "банкротство",
    CompanyStatus.REORGANIZING: "реорганизация",
}

KYC_LEVEL_LABELS: Mapping[KycLevel, str] = {
    KycLevel.GREEN: "зелёный",
    KycLevel.YELLOW: "жёлтый",
    KycLevel.RED: "красный",
}

SIGNATURE_STATUS_LABELS: Mapping[SignatureStatus, str] = {
    SignatureStatus.VALID: "верна",
    SignatureStatus.INVALID: "неверна",
    SignatureStatus.ABSENT: "отсутствует",
}

CONSENT_TYPE_LABELS: Mapping[ConsentType, str] = {
    ConsentType.BKI_COMPANY: "БКИ компании",
    ConsentType.BKI_PERSONAL: "БКИ руководителя",
    ConsentType.PERSONAL_DATA: "обработка ПДн",
}
