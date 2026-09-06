import { Type } from "typebox";
import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";

const PLUGIN_ID = "learnbuddy";
const MCP_SERVER_NAME = "learnbuddy";

function runtimeConfig() {
  const baseUrl = (process.env.LEARNBUDDY_BASE_URL || "").trim().replace(/\/$/, "");
  const bridgeToken = (process.env.LEARNBUDDY_BRIDGE_TOKEN || "").trim();
  const accountId = (process.env.LEARNBUDDY_FEISHU_ACCOUNT_ID || "default").trim();
  if (!baseUrl || !bridgeToken) {
    throw new Error("LEARNBUDDY_BASE_URL and LEARNBUDDY_BRIDGE_TOKEN are required");
  }
  return { baseUrl, bridgeToken, accountId };
}

async function postLearnBuddy(path, payload, signal) {
  const config = runtimeConfig();
  const response = await fetch(`${config.baseUrl}${path}`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${config.bridgeToken}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
    signal,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `LearnBuddy request failed (${response.status})`);
  }
  return data;
}

function trustedFeishuSender(ctx) {
  const channel = (ctx.messageChannel || "").trim().toLowerCase();
  const sender = (ctx.requesterSenderId || "").trim();
  // Fail closed: requesterSenderId 只有在 OpenClaw 明确标识为飞书通道时才可信。
  if (!sender || channel !== "feishu") return null;
  return sender;
}

export default definePluginEntry({
  id: PLUGIN_ID,
  name: "LearnBuddy",
  description: "Requester-scoped LearnBuddy learning tools for Feishu users",
  register(api) {
    api.registerMcpServerConnectionResolver({
      serverName: MCP_SERVER_NAME,
      resolve: async (ctx) => {
        const requesterSenderId = trustedFeishuSender(ctx);
        if (!requesterSenderId) return null;
        try {
          const config = runtimeConfig();
          const result = await postLearnBuddy(
            "/api/integrations/openclaw/token",
            {
              requester_sender_id: requesterSenderId,
              account_id: ctx.agentAccountId || config.accountId,
            },
            ctx.signal,
          );
          return {
            url: `${config.baseUrl}/api/mcp`,
            headers: { Authorization: `Bearer ${result.access_token}` },
          };
        } catch {
          return null;
        }
      },
    });

    api.registerTool(
      (ctx) => {
        const requesterSenderId = trustedFeishuSender(ctx);
        if (!requesterSenderId) return null;
        return {
          name: "learnbuddy_bind",
          description: "Bind the current trusted Feishu sender to a LearnBuddy account using a one-time code from the LearnBuddy account page.",
          parameters: Type.Object(
            {
              code: Type.String({
                description: "One-time binding code in the form LB-XXXX-XXXX-XXXX",
                minLength: 12,
                maxLength: 32,
              }),
            },
            { additionalProperties: false },
          ),
          async execute(_toolCallId, params) {
            try {
              const config = runtimeConfig();
              const result = await postLearnBuddy("/api/integrations/openclaw/bind", {
                code: params.code,
                requester_sender_id: requesterSenderId,
                account_id: ctx.agentAccountId || config.accountId,
              });
              const details = {
                ok: true,
                binding_id: result.binding.id,
                status: result.binding.status,
              };
              return {
                content: [{
                  type: "text",
                  text: "绑定成功。你现在可以让我查看学习计划、记录进度或调整个人提醒。",
                }],
                details,
              };
            } catch (error) {
              const message = error instanceof Error ? error.message : "绑定失败";
              return {
                content: [{ type: "text", text: `绑定失败：${message}` }],
                details: { ok: false },
                isError: true,
              };
            }
          },
        };
      },
      { name: "learnbuddy_bind" },
    );
  },
});
