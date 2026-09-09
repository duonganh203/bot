CREATE TABLE `agent_decisions` (
	`sequence` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`id` text NOT NULL,
	`action` text NOT NULL,
	`symbol` text,
	`amount_usd` text,
	`price` text,
	`confidence` real NOT NULL,
	`risk_level` text NOT NULL,
	`rationale` text NOT NULL,
	`source` text NOT NULL,
	`status` text NOT NULL,
	`trade_id` text,
	`rejection_code` text,
	`rejection_reason` text,
	`created_at` text NOT NULL,
	FOREIGN KEY (`trade_id`) REFERENCES `trades`(`id`) ON UPDATE no action ON DELETE no action,
	CONSTRAINT "decision_action" CHECK("agent_decisions"."action" IN ('BUY', 'SELL', 'HOLD')),
	CONSTRAINT "decision_confidence" CHECK("agent_decisions"."confidence" BETWEEN 0 AND 1),
	CONSTRAINT "decision_consistency" CHECK(
    ("agent_decisions"."status" = 'HOLD' AND "agent_decisions"."action" = 'HOLD' AND "agent_decisions"."trade_id" IS NULL AND "agent_decisions"."rejection_code" IS NULL)
    OR ("agent_decisions"."status" = 'EXECUTED' AND "agent_decisions"."action" IN ('BUY', 'SELL') AND "agent_decisions"."trade_id" IS NOT NULL AND "agent_decisions"."rejection_code" IS NULL)
    OR ("agent_decisions"."status" = 'REJECTED' AND "agent_decisions"."action" IN ('BUY', 'SELL') AND "agent_decisions"."trade_id" IS NULL AND "agent_decisions"."rejection_code" IS NOT NULL AND "agent_decisions"."rejection_reason" IS NOT NULL)
  )
);
--> statement-breakpoint
CREATE UNIQUE INDEX `agent_decisions_id_unique` ON `agent_decisions` (`id`);--> statement-breakpoint
CREATE UNIQUE INDEX `agent_decisions_trade_id_unique` ON `agent_decisions` (`trade_id`);--> statement-breakpoint
CREATE TABLE `market_prices` (
	`symbol` text PRIMARY KEY NOT NULL,
	`price` text NOT NULL,
	`as_of` text NOT NULL,
	`source` text NOT NULL,
	CONSTRAINT "market_symbol" CHECK("market_prices"."symbol" IN ('BTCUSDT', 'ETHUSDT')),
	CONSTRAINT "market_positive_price" CHECK(CAST("market_prices"."price" AS REAL) > 0)
);
--> statement-breakpoint
CREATE TABLE `portfolio` (
	`id` integer PRIMARY KEY NOT NULL,
	`initial_capital` text NOT NULL,
	`cash` text NOT NULL,
	`realized_pnl` text NOT NULL,
	`total_fees` text NOT NULL,
	`version` integer DEFAULT 0 NOT NULL,
	`created_at` text NOT NULL,
	`updated_at` text NOT NULL,
	CONSTRAINT "singleton_portfolio" CHECK("portfolio"."id" = 1),
	CONSTRAINT "nonnegative_cash" CHECK(CAST("portfolio"."cash" AS REAL) >= 0),
	CONSTRAINT "fixed_initial_capital" CHECK("portfolio"."initial_capital" = '50')
);
--> statement-breakpoint
CREATE TABLE `positions` (
	`symbol` text PRIMARY KEY NOT NULL,
	`quantity` text NOT NULL,
	`cost_basis_usd` text NOT NULL,
	`entry_fees_usd` text NOT NULL,
	`average_entry_price` text NOT NULL,
	`created_at` text NOT NULL,
	`updated_at` text NOT NULL,
	CONSTRAINT "position_symbol" CHECK("positions"."symbol" IN ('BTCUSDT', 'ETHUSDT')),
	CONSTRAINT "positive_quantity" CHECK(CAST("positions"."quantity" AS REAL) > 0),
	CONSTRAINT "nonnegative_cost" CHECK(CAST("positions"."cost_basis_usd" AS REAL) >= 0),
	CONSTRAINT "nonnegative_entry_fees" CHECK(CAST("positions"."entry_fees_usd" AS REAL) >= 0)
);
--> statement-breakpoint
CREATE TABLE `trades` (
	`sequence` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`id` text NOT NULL,
	`symbol` text NOT NULL,
	`side` text NOT NULL,
	`quantity` text NOT NULL,
	`price` text NOT NULL,
	`gross_usd` text NOT NULL,
	`fee_usd` text NOT NULL,
	`net_usd` text NOT NULL,
	`realized_pnl` text NOT NULL,
	`created_at` text NOT NULL,
	CONSTRAINT "trade_symbol" CHECK("trades"."symbol" IN ('BTCUSDT', 'ETHUSDT')),
	CONSTRAINT "trade_side" CHECK("trades"."side" IN ('BUY', 'SELL')),
	CONSTRAINT "trade_positive_quantity" CHECK(CAST("trades"."quantity" AS REAL) > 0),
	CONSTRAINT "trade_positive_price" CHECK(CAST("trades"."price" AS REAL) > 0),
	CONSTRAINT "trade_order_limit" CHECK(CAST("trades"."gross_usd" AS REAL) > 0 AND CAST("trades"."gross_usd" AS REAL) <= 5)
);
--> statement-breakpoint
CREATE UNIQUE INDEX `trades_id_unique` ON `trades` (`id`);--> statement-breakpoint
CREATE INDEX `trades_created_at_idx` ON `trades` (`created_at`);--> statement-breakpoint
CREATE INDEX `trades_symbol_sequence_idx` ON `trades` (`symbol`,`sequence`);