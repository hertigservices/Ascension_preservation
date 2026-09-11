import { env } from "cloudflare:workers";
import { handle } from "@/lib/intake-api.mjs";
async function route(request: Request) {
  const path = new URL(request.url).pathname.replace(/^\/api\//, "");
  return handle(request, env, path);
}
export const GET = route;
export const POST = route;
export const PUT = route;
