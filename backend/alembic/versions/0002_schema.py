"""Таблицы пользователей, согласий, заявок, договоров и аудита (BACKEND_SPEC, раздел 4)

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0002'
down_revision: str | None = '0001'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('audit_log',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('actor_id', sa.Uuid(), nullable=True),
    sa.Column('action', sa.String(length=64), nullable=False),
    sa.Column('target_id', sa.Uuid(), nullable=False),
    sa.Column('details', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_audit_log'))
    )
    op.create_index(op.f('ix_audit_log_target_id'), 'audit_log', ['target_id'], unique=False)
    op.create_table('users',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('login', sa.String(length=64), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('role', sa.String(length=32), nullable=False),
    sa.Column('full_name', sa.String(length=255), nullable=False),
    sa.Column('passport_verified', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_users')),
    sa.UniqueConstraint('login', name=op.f('uq_users_login'))
    )
    op.create_table('applications',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('applicant_id', sa.Uuid(), nullable=False),
    sa.Column('inn', sa.String(length=12), nullable=False),
    sa.Column('company_kind', sa.String(length=32), nullable=False),
    sa.Column('purpose', sa.String(length=32), nullable=False),
    sa.Column('product_code', sa.String(length=32), nullable=False),
    sa.Column('variant', sa.String(length=32), nullable=False),
    sa.Column('amount', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('term_months', sa.Integer(), nullable=False),
    sa.Column('owner_guarantee', sa.Boolean(), nullable=False),
    sa.Column('collateral_type', sa.String(length=32), nullable=True),
    sa.Column('collateral_description', sa.Text(), nullable=True),
    sa.Column('collateral_value', sa.Numeric(precision=18, scale=2), nullable=True),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('accepted_offer_id', sa.Uuid(), nullable=True),
    sa.Column('contract_id', sa.Uuid(), nullable=True),
    sa.Column('scoring_total', sa.Integer(), nullable=True),
    sa.Column('scoring_risk_class', sa.String(length=1), nullable=True),
    sa.Column('scoring_debt_load', sa.Float(), nullable=True),
    sa.ForeignKeyConstraint(['applicant_id'], ['users.id'], name=op.f('fk_applications_applicant_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_applications'))
    )
    op.create_index(op.f('ix_applications_applicant_id'), 'applications', ['applicant_id'], unique=False)
    op.create_index('ix_applications_inn_status', 'applications', ['inn', 'status'], unique=False)
    op.create_table('consents',
    sa.Column('inn', sa.String(length=12), nullable=False),
    sa.Column('given_by', sa.Uuid(), nullable=False),
    sa.Column('type', sa.String(length=32), nullable=False),
    sa.Column('given_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['given_by'], ['users.id'], name=op.f('fk_consents_given_by_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('inn', 'given_by', 'type', name=op.f('pk_consents'))
    )
    op.create_table('user_companies',
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('inn', sa.String(length=12), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=500), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('is_director', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_user_companies_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', 'inn', name=op.f('pk_user_companies'))
    )
    op.create_table('application_consents',
    sa.Column('application_id', sa.Uuid(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('type', sa.String(length=32), nullable=False),
    sa.Column('given_by', sa.Uuid(), nullable=False),
    sa.Column('given_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['application_id'], ['applications.id'], name=op.f('fk_application_consents_application_id_applications'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('application_id', 'position', name=op.f('pk_application_consents'))
    )
    op.create_table('bank_statements',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('application_id', sa.Uuid(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('file_name', sa.String(length=255), nullable=False),
    sa.Column('file_key', sa.String(length=512), nullable=False),
    sa.Column('bank_name', sa.String(length=255), nullable=False),
    sa.Column('account_inn', sa.String(length=12), nullable=False),
    sa.Column('period_from', sa.Date(), nullable=False),
    sa.Column('period_to', sa.Date(), nullable=False),
    sa.Column('signature_status', sa.String(length=32), nullable=False),
    sa.Column('uploaded_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['application_id'], ['applications.id'], name=op.f('fk_bank_statements_application_id_applications'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_bank_statements'))
    )
    op.create_index(op.f('ix_bank_statements_application_id'), 'bank_statements', ['application_id'], unique=False)
    op.create_table('check_results',
    sa.Column('application_id', sa.Uuid(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('code', sa.String(length=64), nullable=False),
    sa.Column('stage', sa.String(length=32), nullable=False),
    sa.Column('expected', sa.Text(), nullable=False),
    sa.Column('actual', sa.Text(), nullable=False),
    sa.Column('outcome', sa.String(length=32), nullable=False),
    sa.ForeignKeyConstraint(['application_id'], ['applications.id'], name=op.f('fk_check_results_application_id_applications'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('application_id', 'position', name=op.f('pk_check_results'))
    )
    op.create_table('contracts',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('application_id', sa.Uuid(), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('number', sa.String(length=64), nullable=False),
    sa.Column('product_code', sa.String(length=32), nullable=False),
    sa.Column('variant', sa.String(length=32), nullable=False),
    sa.Column('rate', sa.Numeric(precision=5, scale=2), nullable=False),
    sa.Column('term_months', sa.Integer(), nullable=False),
    sa.Column('start_date', sa.Date(), nullable=False),
    sa.Column('contract_date', sa.Date(), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('principal', sa.Numeric(precision=18, scale=2), nullable=True),
    sa.Column('outstanding', sa.Numeric(precision=18, scale=2), nullable=True),
    sa.Column('limit_amount', sa.Numeric(precision=18, scale=2), nullable=True),
    sa.Column('available', sa.Numeric(precision=18, scale=2), nullable=True),
    sa.ForeignKeyConstraint(['application_id'], ['applications.id'], name=op.f('fk_contracts_application_id_applications')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_contracts')),
    sa.UniqueConstraint('application_id', name=op.f('uq_contracts_application_id')),
    sa.UniqueConstraint('number', name=op.f('uq_contracts_number'))
    )
    op.create_table('decisions',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('application_id', sa.Uuid(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('rule_code', sa.String(length=64), nullable=False),
    sa.Column('outcome', sa.String(length=32), nullable=False),
    sa.Column('rate', sa.Numeric(precision=5, scale=2), nullable=True),
    sa.Column('reasons', postgresql.ARRAY(sa.String(length=64)), nullable=False),
    sa.Column('decided_by', sa.Uuid(), nullable=True),
    sa.Column('comment', sa.Text(), nullable=True),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['application_id'], ['applications.id'], name=op.f('fk_decisions_application_id_applications'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_decisions'))
    )
    op.create_index(op.f('ix_decisions_application_id'), 'decisions', ['application_id'], unique=False)
    op.create_table('info_requests',
    sa.Column('application_id', sa.Uuid(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('items', postgresql.ARRAY(sa.String(length=64)), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('created_by', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['application_id'], ['applications.id'], name=op.f('fk_info_requests_application_id_applications'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('application_id', 'position', name=op.f('pk_info_requests'))
    )
    op.create_table('scoring_factors',
    sa.Column('application_id', sa.Uuid(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('code', sa.String(length=64), nullable=False),
    sa.Column('label', sa.String(length=255), nullable=False),
    sa.Column('value', sa.String(length=255), nullable=False),
    sa.Column('points', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['application_id'], ['applications.id'], name=op.f('fk_scoring_factors_application_id_applications'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('application_id', 'position', name=op.f('pk_scoring_factors'))
    )
    op.create_table('source_snapshots',
    sa.Column('application_id', sa.Uuid(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('source', sa.String(length=32), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['application_id'], ['applications.id'], name=op.f('fk_source_snapshots_application_id_applications'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('application_id', 'position', name=op.f('pk_source_snapshots'))
    )
    op.create_table('status_transitions',
    sa.Column('application_id', sa.Uuid(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('from_status', sa.String(length=32), nullable=False),
    sa.Column('to_status', sa.String(length=32), nullable=False),
    sa.Column('actor_id', sa.Uuid(), nullable=True),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['application_id'], ['applications.id'], name=op.f('fk_status_transitions_application_id_applications'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('application_id', 'position', name=op.f('pk_status_transitions'))
    )
    op.create_table('contract_versions',
    sa.Column('contract_id', sa.Uuid(), nullable=False),
    sa.Column('version_no', sa.Integer(), nullable=False),
    sa.Column('document_key', sa.String(length=512), nullable=False),
    sa.Column('changes', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('author_id', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['contract_id'], ['contracts.id'], name=op.f('fk_contract_versions_contract_id_contracts'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('contract_id', 'version_no', name=op.f('pk_contract_versions'))
    )
    op.create_table('offer_options',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('decision_id', sa.Uuid(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('product_code', sa.String(length=32), nullable=False),
    sa.Column('variant', sa.String(length=32), nullable=False),
    sa.Column('amount', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('term_months', sa.Integer(), nullable=False),
    sa.Column('rate', sa.Numeric(precision=5, scale=2), nullable=True),
    sa.ForeignKeyConstraint(['decision_id'], ['decisions.id'], name=op.f('fk_offer_options_decision_id_decisions'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_offer_options'))
    )
    op.create_index(op.f('ix_offer_options_decision_id'), 'offer_options', ['decision_id'], unique=False)
    op.create_table('operations',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('contract_id', sa.Uuid(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('type', sa.String(length=32), nullable=False),
    sa.Column('amount', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('contract_date', sa.Date(), nullable=False),
    sa.Column('actor_id', sa.Uuid(), nullable=True),
    sa.Column('balance_after', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.ForeignKeyConstraint(['contract_id'], ['contracts.id'], name=op.f('fk_operations_contract_id_contracts'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_operations'))
    )
    op.create_index(op.f('ix_operations_contract_id'), 'operations', ['contract_id'], unique=False)
    op.create_table('statement_operations',
    sa.Column('statement_id', sa.Uuid(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('date', sa.Date(), nullable=False),
    sa.Column('amount', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('direction', sa.String(length=32), nullable=False),
    sa.Column('counterparty_inn', sa.String(length=12), nullable=False),
    sa.ForeignKeyConstraint(['statement_id'], ['bank_statements.id'], name=op.f('fk_statement_operations_statement_id_bank_statements'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('statement_id', 'position', name=op.f('pk_statement_operations'))
    )
    op.create_table('tranches',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('contract_id', sa.Uuid(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('amount', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('issued_at', sa.Date(), nullable=False),
    sa.Column('due_at', sa.Date(), nullable=False),
    sa.Column('outstanding', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.ForeignKeyConstraint(['contract_id'], ['contracts.id'], name=op.f('fk_tranches_contract_id_contracts'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_tranches'))
    )
    op.create_index(op.f('ix_tranches_contract_id'), 'tranches', ['contract_id'], unique=False)
    op.create_table('schedule_items',
    sa.Column('contract_id', sa.Uuid(), nullable=False),
    sa.Column('version_no', sa.Integer(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('due_date', sa.Date(), nullable=False),
    sa.Column('principal', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('interest', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('balance', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.ForeignKeyConstraint(['contract_id', 'version_no'], ['contract_versions.contract_id', 'contract_versions.version_no'], name=op.f('fk_schedule_items_contract_id_contract_versions'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('contract_id', 'version_no', 'position', name=op.f('pk_schedule_items'))
    )


def downgrade() -> None:
    op.drop_table('schedule_items')
    op.drop_index(op.f('ix_tranches_contract_id'), table_name='tranches')
    op.drop_table('tranches')
    op.drop_table('statement_operations')
    op.drop_index(op.f('ix_operations_contract_id'), table_name='operations')
    op.drop_table('operations')
    op.drop_index(op.f('ix_offer_options_decision_id'), table_name='offer_options')
    op.drop_table('offer_options')
    op.drop_table('contract_versions')
    op.drop_table('status_transitions')
    op.drop_table('source_snapshots')
    op.drop_table('scoring_factors')
    op.drop_table('info_requests')
    op.drop_index(op.f('ix_decisions_application_id'), table_name='decisions')
    op.drop_table('decisions')
    op.drop_table('contracts')
    op.drop_table('check_results')
    op.drop_index(op.f('ix_bank_statements_application_id'), table_name='bank_statements')
    op.drop_table('bank_statements')
    op.drop_table('application_consents')
    op.drop_table('user_companies')
    op.drop_table('consents')
    op.drop_index('ix_applications_inn_status', table_name='applications')
    op.drop_index(op.f('ix_applications_applicant_id'), table_name='applications')
    op.drop_table('applications')
    op.drop_table('users')
    op.drop_index(op.f('ix_audit_log_target_id'), table_name='audit_log')
    op.drop_table('audit_log')
