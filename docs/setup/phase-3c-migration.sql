BEGIN;

-- Running upgrade 0006 -> 0007

ALTER TABLE paper_trades ADD COLUMN booking_mode VARCHAR(16);

ALTER TABLE paper_trades ADD COLUMN slippage_inr FLOAT;

ALTER TABLE portfolio_snapshots ADD COLUMN capital_used_inr FLOAT;

ALTER TABLE trade_events ADD COLUMN lots INTEGER;

ALTER TABLE trade_events ADD COLUMN trigger_level FLOAT;

ALTER TABLE trade_events ADD COLUMN slippage_inr FLOAT;

ALTER TABLE trade_events ADD COLUMN gap BOOLEAN;

UPDATE alembic_version SET version_num='0007' WHERE alembic_version.version_num = '0006';

COMMIT;

