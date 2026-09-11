declare namespace Cloudflare {
  interface Env {
    DB?: D1Database;
    BUCKET?: R2Bucket;
    UPLOADS_ENABLED?: string;
    ALLOWED_ORIGIN?: string;
    TURNSTILE_SITE_KEY?: string;
    TURNSTILE_SECRET?: string;
    RATE_SECRET?: string;
    COLLECTOR_TOKEN?: string;
  }
}
