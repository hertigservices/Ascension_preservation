import {build} from "esbuild";
import {writeFile} from "node:fs/promises";
await build({entryPoints:["lib/intake-api.mjs"],outfile:"dist/server/retention.js",bundle:true,format:"esm",platform:"browser",target:"es2022"});
await writeFile("dist/server/production.js", `import app from "./index.js";
import {cleanup} from "./retention.js";
export default {fetch:(r,e,c)=>app.fetch(r,e,c),scheduled:(event,env,ctx)=>ctx.waitUntil(cleanup(env))};
`);
