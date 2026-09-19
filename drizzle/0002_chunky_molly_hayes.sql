-- Foreign keys are disabled by openDatabase before the migration transaction.
CREATE TABLE `__new_market_prices` (
	`symbol` text PRIMARY KEY NOT NULL,
	`price` text NOT NULL,
	`as_of` text NOT NULL,
	`source` text NOT NULL,
	CONSTRAINT "market_symbol" CHECK("__new_market_prices"."symbol" IN ('BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT')),
	CONSTRAINT "market_positive_price" CHECK(CAST("__new_market_prices"."price" AS REAL) > 0)
);
--> statement-breakpoint
INSERT INTO `__new_market_prices`("symbol", "price", "as_of", "source") SELECT "symbol", "price", "as_of", "source" FROM `market_prices`;--> statement-breakpoint
DROP TABLE `market_prices`;--> statement-breakpoint
ALTER TABLE `__new_market_prices` RENAME TO `market_prices`;--> statement-breakpoint
CREATE TABLE `__new_positions` (
	`symbol` text PRIMARY KEY NOT NULL,
	`quantity` text NOT NULL,
	`cost_basis_usd` text NOT NULL,
	`entry_fees_usd` text NOT NULL,
	`average_entry_price` text NOT NULL,
	`created_at` text NOT NULL,
	`updated_at` text NOT NULL,
	CONSTRAINT "position_symbol" CHECK("__new_positions"."symbol" IN ('BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT')),
	CONSTRAINT "positive_quantity" CHECK(CAST("__new_positions"."quantity" AS REAL) > 0),
	CONSTRAINT "nonnegative_cost" CHECK(CAST("__new_positions"."cost_basis_usd" AS REAL) >= 0),
	CONSTRAINT "nonnegative_entry_fees" CHECK(CAST("__new_positions"."entry_fees_usd" AS REAL) >= 0)
);
--> statement-breakpoint
INSERT INTO `__new_positions`("symbol", "quantity", "cost_basis_usd", "entry_fees_usd", "average_entry_price", "created_at", "updated_at") SELECT "symbol", "quantity", "cost_basis_usd", "entry_fees_usd", "average_entry_price", "created_at", "updated_at" FROM `positions`;--> statement-breakpoint
DROP TABLE `positions`;--> statement-breakpoint
ALTER TABLE `__new_positions` RENAME TO `positions`;--> statement-breakpoint
CREATE TABLE `__new_trades` (
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
	CONSTRAINT "trade_symbol" CHECK("__new_trades"."symbol" IN ('BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT')),
	CONSTRAINT "trade_side" CHECK("__new_trades"."side" IN ('BUY', 'SELL')),
	CONSTRAINT "trade_positive_quantity" CHECK(CAST("__new_trades"."quantity" AS REAL) > 0),
	CONSTRAINT "trade_positive_price" CHECK(CAST("__new_trades"."price" AS REAL) > 0),
	CONSTRAINT "trade_order_limit" CHECK(CAST("__new_trades"."gross_usd" AS REAL) > 0 AND CAST("__new_trades"."gross_usd" AS REAL) <= 5)
);
--> statement-breakpoint
INSERT INTO `__new_trades`("sequence", "id", "symbol", "side", "quantity", "price", "gross_usd", "fee_usd", "net_usd", "realized_pnl", "created_at") SELECT "sequence", "id", "symbol", "side", "quantity", "price", "gross_usd", "fee_usd", "net_usd", "realized_pnl", "created_at" FROM `trades`;--> statement-breakpoint
DROP TABLE `trades`;--> statement-breakpoint
ALTER TABLE `__new_trades` RENAME TO `trades`;--> statement-breakpoint
CREATE UNIQUE INDEX `trades_id_unique` ON `trades` (`id`);--> statement-breakpoint
CREATE INDEX `trades_created_at_idx` ON `trades` (`created_at`);--> statement-breakpoint
CREATE INDEX `trades_symbol_sequence_idx` ON `trades` (`symbol`,`sequence`);
--> statement-breakpoint
CREATE TABLE `__migration_fk_check` (`violations` integer NOT NULL CHECK (`violations` = 0));
--> statement-breakpoint
INSERT INTO `__migration_fk_check` SELECT count(*) FROM pragma_foreign_key_check;
--> statement-breakpoint
DROP TABLE `__migration_fk_check`;
