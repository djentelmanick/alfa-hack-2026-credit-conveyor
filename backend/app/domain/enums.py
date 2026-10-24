from enum import StrEnum


class Role(StrEnum):
    CLIENT = "CLIENT"
    ANALYST = "ANALYST"


class CompanyKind(StrEnum):
    LLC = "LLC"
    IE = "IE"


class CompanyStatus(StrEnum):
    ACTIVE = "ACTIVE"
    LIQUIDATED = "LIQUIDATED"
    BANKRUPT = "BANKRUPT"
    REORGANIZING = "REORGANIZING"


class ProductCode(StrEnum):
    LUMP_SUM = "LUMP_SUM"
    REVOLVING_LINE = "REVOLVING_LINE"


class Variant(StrEnum):
    UNSECURED = "UNSECURED"
    SECURED = "SECURED"


class Purpose(StrEnum):
    ONE_TIME_PURCHASE = "ONE_TIME_PURCHASE"
    DEVELOPMENT = "DEVELOPMENT"
    REFINANCING = "REFINANCING"
    REGULAR_PURCHASES = "REGULAR_PURCHASES"
    SEASONAL_GAP = "SEASONAL_GAP"


class CollateralType(StrEnum):
    REAL_ESTATE = "REAL_ESTATE"
    VEHICLE = "VEHICLE"
    EQUIPMENT = "EQUIPMENT"
    GOODS = "GOODS"


class ConsentType(StrEnum):
    BKI_COMPANY = "BKI_COMPANY"
    BKI_PERSONAL = "BKI_PERSONAL"
    PERSONAL_DATA = "PERSONAL_DATA"


class ApplicationStatus(StrEnum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    CHECKING = "CHECKING"
    NEEDS_INFO = "NEEDS_INFO"
    SCORING = "SCORING"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    APPROVED = "APPROVED"
    COUNTER_OFFER = "COUNTER_OFFER"
    SIGNING = "SIGNING"
    ISSUED = "ISSUED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class AllowedAction(StrEnum):
    EDIT = "EDIT"
    UPLOAD_STATEMENT = "UPLOAD_STATEMENT"
    SUBMIT = "SUBMIT"
    ACCEPT = "ACCEPT"
    CANCEL = "CANCEL"
    SIGN = "SIGN"


class RiskClass(StrEnum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"


class DecisionOutcome(StrEnum):
    APPROVED = "APPROVED"
    COUNTER_OFFER = "COUNTER_OFFER"
    NEEDS_INFO = "NEEDS_INFO"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    REJECTED = "REJECTED"


class SignatureStatus(StrEnum):
    VALID = "VALID"
    INVALID = "INVALID"
    ABSENT = "ABSENT"


class CheckStage(StrEnum):
    FORM = "FORM"
    CHECKING = "CHECKING"
    POST_SCORING = "POST_SCORING"


class CheckOutcome(StrEnum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    STOP_FACTOR = "STOP_FACTOR"
    NO_DATA = "NO_DATA"


class SourceType(StrEnum):
    EGRUL = "EGRUL"
    BKI = "BKI"
    TURNOVER = "TURNOVER"
    FNS = "FNS"
    GIR_BO = "GIR_BO"
    FSSP = "FSSP"
    CBR_KYC = "CBR_KYC"
    ROSFIN = "ROSFIN"
    BANK_SIGNATURE = "BANK_SIGNATURE"


class FetchStatus(StrEnum):
    OK = "OK"
    FAILED = "FAILED"


class KycLevel(StrEnum):
    """Уровень риска на платформе ЦБ «Знай своего клиента»."""

    GREEN = "GREEN"
    YELLOW = "YELLOW"
    RED = "RED"


class Direction(StrEnum):
    INCOMING = "INCOMING"
    OUTGOING = "OUTGOING"


class ContractStatus(StrEnum):
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"


class OperationType(StrEnum):
    DISBURSEMENT = "DISBURSEMENT"
    SCHEDULED_PAYMENT = "SCHEDULED_PAYMENT"
    PARTIAL_PREPAYMENT = "PARTIAL_PREPAYMENT"
    FULL_REPAYMENT = "FULL_REPAYMENT"
    LIMIT_OPENED = "LIMIT_OPENED"
    TRANCHE_DRAWN = "TRANCHE_DRAWN"
    INTEREST_ACCRUED = "INTEREST_ACCRUED"
    TRANCHE_REPAID = "TRANCHE_REPAID"
    CONTRACT_EDITED = "CONTRACT_EDITED"
