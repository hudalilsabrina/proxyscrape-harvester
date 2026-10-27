import http from "node:http";
import url from "node:url";

const PORT = process.env.PORT || 3333;
const emails = [];
const waitingClients = new Map();

function extractOtp(subject, body) {
  const text = `${subject || ""}\n${body || ""}`;
  const m1 = text.match(/(?:verification\s*code)[:\s*#]+([a-f0-9]{10})\b/i);
  if (m1) return m1[1];
  const m2 = text.match(/(?:code|otp|pin|verifikasi)[:\s*#]+([0-9]{4,8}|[a-f0-9]{10})\b/i);
  if (m2) return m2[1];
  return null;
}

const server = http.createServer(async (req, res) => {
  const parsed = url.parse(req.url, true);

  if (req.method === "POST" && (parsed.pathname === "/api/incoming-email" || parsed.pathname === "/incoming-email")) {
    let body = "";
    req.on("data", chunk => { body += chunk; });
    req.on("end", () => {
      try {
        const payload = JSON.parse(body);
        const toEmail = (payload.to || "").toLowerCase().trim();
        const raw = payload.raw_snippet || payload.body || "";
        const subject = payload.subject || "";
        const otp = extractOtp(subject, raw);

        const record = {
          to: toEmail,
          from: payload.from || "",
          subject: subject,
          otp: otp,
          snippet: raw.slice(0, 500),
          received_at: new Date().toISOString()
        };

        emails.unshift(record);
        if (emails.length > 500) emails.pop();

        console.log(`[MAIL] Received email for ${toEmail} | Subject: "${subject}" | OTP: ${otp || "-"}`);

        if (waitingClients.has(toEmail)) {
          const clientRes = waitingClients.get(toEmail);
          waitingClients.delete(toEmail);
          clientRes.writeHead(200, { "Content-Type": "application/json" });
          clientRes.end(JSON.stringify({ status: "found", success: true, otp: otp }));
        }

        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ status: "ok", success: true, otp: otp }));
      } catch (e) {
        res.writeHead(400, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ error: e.message }));
      }
    });
    return;
  }

  if (req.method === "GET" && parsed.pathname === "/api/wait-otp") {
    const toEmail = (parsed.query.to || "").toLowerCase().trim();
    const timeoutSec = parseInt(parsed.query.timeout || "10", 10);

    const existing = emails.find(e => e.to === toEmail && e.otp);
    if (existing) {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ status: "found", success: true, otp: existing.otp }));
      return;
    }

    const timer = setTimeout(() => {
      if (waitingClients.has(toEmail)) {
        waitingClients.delete(toEmail);
        res.writeHead(408, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ status: "timeout", success: false }));
      }
    }, timeoutSec * 1000);

    waitingClients.set(toEmail, res);
    req.on("close", () => {
      clearTimeout(timer);
      waitingClients.delete(toEmail);
    });
    return;
  }

  if (req.method === "GET" && parsed.pathname === "/api/message") {
    const toEmail = (parsed.query.to || "").toLowerCase().trim();
    const existing = emails.find(e => e.to === toEmail);
    if (existing) {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ status: "found", success: true, message: existing }));
      return;
    }
    res.writeHead(404, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ status: "not_found", success: false }));
    return;
  }

  res.writeHead(404, { "Content-Type": "application/json" });
  res.end(JSON.stringify({ error: "Not found" }));
});

server.listen(PORT, "0.0.0.0", () => {
  console.log(`[MAIL RECEIVER] Listening on http://0.0.0.0:${PORT}`);
});
