// A git server over smart HTTP for the plugins check (plugins.mjs): `git http-backend` behind
// node:http, serving every repository under a folder, read-only. Dumb HTTP can't serve shallow
// clones, which is how Gen9 fetches.
//
//   node fixtures/git-server.mjs <folder> <port>      serves <folder>/<name>.git at /<name>.git
//
// GET /redirect/<anything> answers 301 to /<anything>, for the check that Gen9 follows none.
import { spawn } from "node:child_process";
import { createServer } from "node:http";

export function serveGit(root, port, host = "0.0.0.0") {
  const server = createServer((req, res) => {
    const url = new URL(req.url, "http://x");
    if (url.pathname.startsWith("/redirect/")) {
      res.writeHead(301, { Location: url.pathname.slice("/redirect".length) + url.search });
      res.end();
      return;
    }
    if (url.pathname.includes("git-receive-pack") || url.searchParams.get("service") === "git-receive-pack") {
      res.writeHead(403).end("read-only");
      return;
    }
    const cgi = spawn("git", ["http-backend"], {
      env: {
        PATH: process.env.PATH,
        GIT_PROJECT_ROOT: root,
        GIT_HTTP_EXPORT_ALL: "1",
        GIT_CONFIG_NOSYSTEM: "1",
        // Partial clones, as Gen9's sparse fetch of a git-subdir source asks for
        GIT_CONFIG_COUNT: "1",
        GIT_CONFIG_KEY_0: "uploadpack.allowFilter",
        GIT_CONFIG_VALUE_0: "true",
        REQUEST_METHOD: req.method,
        PATH_INFO: decodeURIComponent(url.pathname),
        QUERY_STRING: url.search.slice(1),
        CONTENT_TYPE: req.headers["content-type"] ?? "",
        CONTENT_LENGTH: req.headers["content-length"] ?? "",
        HTTP_CONTENT_ENCODING: req.headers["content-encoding"] ?? "",
        GIT_PROTOCOL: req.headers["git-protocol"] ?? "",
        REMOTE_ADDR: req.socket.remoteAddress ?? "",
      },
    });
    req.pipe(cgi.stdin);
    let head = Buffer.alloc(0);
    let sent = false;
    cgi.stdout.on("data", (chunk) => {
      if (sent) return void res.write(chunk);
      head = Buffer.concat([head, chunk]);
      const end = head.indexOf("\r\n\r\n");
      if (end < 0) return;
      let status = 200;
      const headers = {};
      for (const line of head.subarray(0, end).toString().split("\r\n")) {
        const i = line.indexOf(":");
        const [name, value] = [line.slice(0, i).trim(), line.slice(i + 1).trim()];
        if (name.toLowerCase() === "status") status = parseInt(value, 10);
        else headers[name] = value;
      }
      res.writeHead(status, headers);
      sent = true;
      res.write(head.subarray(end + 4));
    });
    cgi.stdout.on("end", () => res.end());
    cgi.on("error", () => res.writeHead(500).end());
  });
  return new Promise((resolve) => server.listen(port, host, () => resolve(server)));
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const [root, port] = process.argv.slice(2);
  await serveGit(root, Number(port));
  console.log(`serving ${root} on :${port}`);
}
