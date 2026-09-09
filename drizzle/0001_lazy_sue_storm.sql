CREATE TABLE `context_snapshots` (
	`id` text PRIMARY KEY NOT NULL,
	`portfolio_version` integer NOT NULL,
	`payload` text NOT NULL,
	`created_at` text NOT NULL
);
--> statement-breakpoint
CREATE INDEX `context_snapshots_created_at_idx` ON `context_snapshots` (`created_at`);--> statement-breakpoint
CREATE TABLE `signal_receipts` (
	`key` text PRIMARY KEY NOT NULL,
	`request_hash` text NOT NULL,
	`decision_id` text NOT NULL,
	`result` text NOT NULL,
	`created_at` text NOT NULL,
	FOREIGN KEY (`decision_id`) REFERENCES `agent_decisions`(`id`) ON UPDATE no action ON DELETE no action
);
--> statement-breakpoint
CREATE UNIQUE INDEX `signal_receipts_decision_id_unique` ON `signal_receipts` (`decision_id`);--> statement-breakpoint
ALTER TABLE `agent_decisions` ADD `strategy_id` text DEFAULT 'default' NOT NULL;--> statement-breakpoint
ALTER TABLE `agent_decisions` ADD `strategy_version` text DEFAULT 'unversioned' NOT NULL;--> statement-breakpoint
ALTER TABLE `agent_decisions` ADD `context_id` text REFERENCES context_snapshots(id);--> statement-breakpoint
ALTER TABLE `agent_decisions` ADD `execution_context` text;--> statement-breakpoint
CREATE INDEX `decisions_strategy_created_at_idx` ON `agent_decisions` (`strategy_id`,`strategy_version`,`created_at`);--> statement-breakpoint
CREATE INDEX `decisions_created_at_idx` ON `agent_decisions` (`created_at`);