export default {
  async email(message, env, ctx) {
    const reader = message.raw.getReader();
    let chunks = [];
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      chunks.push(value);
    }
    const totalLen = chunks.reduce((a, c) => a + c.length, 0);
    const merged = new Uint8Array(totalLen);
    let offset = 0;
    for (const chunk of chunks) {
      merged.set(chunk, offset);
      offset += chunk.length;
    }
    const rawText = new TextDecoder().decode(merged).slice(0, 32000);

    const payload = JSON.stringify({
      to: message.to,
      from: message.from,
      subject: message.headers.get("subject") || "",
      raw_snippet: rawText
    });

    const webhookUrl = env.WEBHOOK_URL || "http://YOUR_SERVER_IP:3333/api/incoming-email";

    try {
      await fetch(webhookUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: payload
      });
    } catch (e) {
      console.error("Webhook forwarding failed:", e);
    }
  }
};
