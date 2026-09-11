CREATE TABLE `submissions` (
	`id` text PRIMARY KEY NOT NULL,
	`upload_hash` text NOT NULL,
	`receipt_hash` text NOT NULL,
	`ip_hash` text NOT NULL,
	`manifest` text NOT NULL,
	`bytes` integer NOT NULL,
	`created` integer NOT NULL,
	`expires` integer NOT NULL,
	`status` text DEFAULT 'uploading' NOT NULL,
	`lease` text,
	`lease_until` integer DEFAULT 0 NOT NULL,
	`commit_sha` text,
	`message` text,
	`attempts` integer DEFAULT 0 NOT NULL
);
--> statement-breakpoint
CREATE INDEX `submission_queue` ON `submissions` (`status`,`lease_until`,`created`);--> statement-breakpoint
CREATE INDEX `submission_quota` ON `submissions` (`ip_hash`,`created`);--> statement-breakpoint
CREATE INDEX `submission_expiry` ON `submissions` (`expires`);