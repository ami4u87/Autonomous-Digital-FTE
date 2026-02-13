/**
 * MCP Email Server — sends emails via Gmail API (OAuth2).
 *
 * A lightweight Express server exposing a single POST /send-email endpoint.
 * Designed to be called by Claude Code / task_processor after human approval.
 *
 * REQUIREMENTS
 * ------------
 *   Node.js v24+
 *   npm init -y && npm install express googleapis dotenv
 *
 * USAGE
 * -----
 *   node mcp_email_server.js
 *
 * ENVIRONMENT (.env or shell)
 * ---------------------------
 *   PORT=3000                     # optional, defaults to 3000
 *   CREDENTIALS_PATH=credentials.json
 *   TOKEN_PATH=token.json
 *   LOG_FILE=email_server.log     # optional, leave empty to skip file logging
 */

"use strict";

const express = require("express");
const { google } = require("googleapis");
const fs = require("fs");
const path = require("path");

// Load .env if present (non-fatal if missing)
try {
  require("dotenv").config();
} catch {
  // dotenv not installed or .env missing — continue with shell env
}

// ------------------------------------------------------------------
// Configuration
// ------------------------------------------------------------------

const PORT = parseInt(process.env.PORT, 10) || 3000;
const CREDENTIALS_PATH = path.resolve(
  process.env.CREDENTIALS_PATH || "credentials.json"
);
const TOKEN_PATH = path.resolve(process.env.TOKEN_PATH || "token.json");
const LOG_FILE = process.env.LOG_FILE || "";
const SCOPES = ["https://www.googleapis.com/auth/gmail.send"];

// ------------------------------------------------------------------
// Logging
// ------------------------------------------------------------------

/**
 * Log a message to stdout and optionally to a file.
 * @param {"INFO"|"WARN"|"ERROR"} level
 * @param {string} message
 */
function log(level, message) {
  const timestamp = new Date().toISOString();
  const line = `${timestamp} [${level}] ${message}`;
  console.log(line);

  if (LOG_FILE) {
    try {
      fs.appendFileSync(LOG_FILE, line + "\n", "utf-8");
    } catch {
      // Swallow write errors — do not crash the server over logging
    }
  }
}

// ------------------------------------------------------------------
// Gmail OAuth2 authentication
// ------------------------------------------------------------------

/** @type {import("googleapis").Auth.OAuth2Client|null} */
let oAuth2Client = null;

/**
 * Build and return an authenticated OAuth2 client.
 * Reuses credentials.json (client secrets) and token.json (refresh/access token).
 * Automatically refreshes the access token when it has expired.
 */
function getAuthClient() {
  if (oAuth2Client) {
    return oAuth2Client;
  }

  // ---- Load client secrets ----
  if (!fs.existsSync(CREDENTIALS_PATH)) {
    throw new Error(
      `Client secrets file not found: ${CREDENTIALS_PATH}\n` +
        "Download it from Google Cloud Console → APIs & Services → Credentials."
    );
  }

  const secretsRaw = JSON.parse(fs.readFileSync(CREDENTIALS_PATH, "utf-8"));

  // Google's download wraps keys under "installed" or "web"
  const secrets = secretsRaw.installed || secretsRaw.web;
  if (!secrets) {
    throw new Error(
      "credentials.json must contain an 'installed' or 'web' key. " +
        "Re-download from Google Cloud Console."
    );
  }

  const { client_id, client_secret, redirect_uris } = secrets;
  oAuth2Client = new google.auth.OAuth2(
    client_id,
    client_secret,
    redirect_uris?.[0] || "urn:ietf:wg:oauth:2.0:oob"
  );

  // ---- Load saved token ----
  if (!fs.existsSync(TOKEN_PATH)) {
    throw new Error(
      `Token file not found: ${TOKEN_PATH}\n` +
        "Generate one by running the Python GmailWatcher first, or use the\n" +
        "manual flow described at the bottom of this file."
    );
  }

  const token = JSON.parse(fs.readFileSync(TOKEN_PATH, "utf-8"));
  oAuth2Client.setCredentials(token);

  // ---- Auto-refresh listener: persist new tokens to disk ----
  oAuth2Client.on("tokens", (newTokens) => {
    log("INFO", "Access token refreshed — saving to disk");
    const merged = { ...token, ...newTokens };
    fs.writeFileSync(TOKEN_PATH, JSON.stringify(merged, null, 2), "utf-8");
  });

  log("INFO", "OAuth2 client initialised");
  return oAuth2Client;
}

// ------------------------------------------------------------------
// Gmail send helper
// ------------------------------------------------------------------

/**
 * Build an RFC 2822 message and send it via the Gmail API.
 *
 * @param {object} params
 * @param {string} params.to       Recipient email address
 * @param {string} params.subject  Email subject line
 * @param {string} params.body     Plain-text email body
 * @param {string} [params.threadId] Optional Gmail thread ID for replies
 * @returns {Promise<{messageId: string, threadId: string}>}
 */
async function sendEmail({ to, subject, body, threadId }) {
  const auth = getAuthClient();

  // Force a token refresh if the current one is expired
  const tokenInfo = auth.credentials;
  if (tokenInfo.expiry_date && Date.now() >= tokenInfo.expiry_date) {
    log("INFO", "Token expired — refreshing before send");
    const { credentials } = await auth.refreshAccessToken();
    auth.setCredentials(credentials);
  }

  const gmail = google.gmail({ version: "v1", auth });

  // Build RFC 2822 raw message
  const messageParts = [
    `To: ${to}`,
    `Subject: ${subject}`,
    "MIME-Version: 1.0",
    "Content-Type: text/plain; charset=UTF-8",
    "",
    body,
  ];
  const rawMessage = messageParts.join("\r\n");

  // Gmail API expects URL-safe base64
  const encoded = Buffer.from(rawMessage)
    .toString("base64")
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");

  const requestBody = { raw: encoded };
  if (threadId) {
    requestBody.threadId = threadId;
  }

  const response = await gmail.users.messages.send({
    userId: "me",
    requestBody,
  });

  return {
    messageId: response.data.id,
    threadId: response.data.threadId,
  };
}

// ------------------------------------------------------------------
// Express server
// ------------------------------------------------------------------

const app = express();
app.use(express.json());

// ---- Security: localhost-only access ----
app.use((req, res, next) => {
  const remoteIp = req.ip || req.connection.remoteAddress || "";
  const allowed = ["127.0.0.1", "::1", "::ffff:127.0.0.1"];

  if (!allowed.includes(remoteIp)) {
    log("WARN", `Blocked request from non-local IP: ${remoteIp}`);
    return res.status(403).json({
      success: false,
      error: "Forbidden — only local requests are allowed",
    });
  }

  next();
});

// ---- Health check ----
app.get("/health", (_req, res) => {
  res.json({ status: "ok", timestamp: new Date().toISOString() });
});

// ---- POST /send-email ----
app.post("/send-email", async (req, res) => {
  const { to, subject, body, threadId } = req.body;

  // Validate required fields
  if (!to || !subject || !body) {
    log("WARN", `Bad request — missing fields (to=${!!to}, subject=${!!subject}, body=${!!body})`);
    return res.status(400).json({
      success: false,
      error: "Missing required fields: to, subject, body",
    });
  }

  // Basic email format check
  if (!to.includes("@")) {
    log("WARN", `Bad request — invalid email: ${to}`);
    return res.status(400).json({
      success: false,
      error: "Invalid email address format",
    });
  }

  log("INFO", `Sending email → to="${to}" subject="${subject}" threadId=${threadId || "none"}`);

  try {
    const result = await sendEmail({ to, subject, body, threadId });
    log("INFO", `Email sent — messageId=${result.messageId}`);
    return res.json({
      success: true,
      messageId: result.messageId,
      threadId: result.threadId,
    });
  } catch (err) {
    const errorMessage = err.message || String(err);

    // Distinguish auth errors from send errors
    if (errorMessage.includes("invalid_grant") || errorMessage.includes("Token")) {
      log("ERROR", `Auth error: ${errorMessage}`);
      return res.status(401).json({
        success: false,
        error: "Authentication failed — token may be expired or revoked. Re-run OAuth flow.",
      });
    }

    log("ERROR", `Send failed: ${errorMessage}`);
    return res.status(500).json({
      success: false,
      error: `Failed to send email: ${errorMessage}`,
    });
  }
});

// ---- 404 catch-all ----
app.use((_req, res) => {
  res.status(404).json({
    success: false,
    error: "Not found. Available endpoints: POST /send-email, GET /health",
  });
});

// ------------------------------------------------------------------
// Start server
// ------------------------------------------------------------------

function start() {
  // Validate auth on startup so we fail fast
  try {
    getAuthClient();
  } catch (err) {
    log("ERROR", err.message);
    process.exit(1);
  }

  app.listen(PORT, "127.0.0.1", () => {
    log("INFO", "=".repeat(60));
    log("INFO", `MCP Email Server running on http://127.0.0.1:${PORT}`);
    log("INFO", `Endpoints: POST /send-email | GET /health`);
    log("INFO", `Credentials: ${CREDENTIALS_PATH}`);
    log("INFO", `Token: ${TOKEN_PATH}`);
    log("INFO", "Accepting local connections only");
    log("INFO", "=".repeat(60));
  });
}

start();


// ======================================================================
// SETUP INSTRUCTIONS
// ======================================================================
//
// 1. INITIALISE THE PROJECT
//
//    cd /path/to/vault
//    npm init -y
//    npm install express googleapis dotenv
//
// 2. COPY credentials.json
//
//    Use the same credentials.json from the Google OAuth setup
//    (see gmail_watcher.py for the full walkthrough).
//    Place it in the same directory as this file.
//
// 3. GENERATE token.json
//
//    Option A — Reuse from Python watcher:
//      If you already ran gmail_watcher.py and scanned the QR / did the
//      OAuth flow, a token.json already exists in your vault.  Copy it
//      here (or point TOKEN_PATH at it).
//
//      IMPORTANT: The Python watcher uses the gmail.readonly scope.
//      This server needs gmail.send.  You may need to re-authorise
//      with the broader scope.  Delete token.json and run the Python
//      watcher with SCOPES including gmail.send, OR use Option B.
//
//    Option B — Manual token generation:
//      Run this one-time script (save as generate_token.js):
//
//        const { google } = require("googleapis");
//        const fs = require("fs");
//        const readline = require("readline");
//
//        const secrets = JSON.parse(fs.readFileSync("credentials.json")).installed;
//        const oAuth2 = new google.auth.OAuth2(
//          secrets.client_id,
//          secrets.client_secret,
//          secrets.redirect_uris[0]
//        );
//
//        const url = oAuth2.generateAuthUrl({
//          access_type: "offline",
//          scope: ["https://www.googleapis.com/auth/gmail.send"],
//        });
//        console.log("Open this URL in your browser:\n", url);
//
//        const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
//        rl.question("Paste the authorisation code: ", async (code) => {
//          const { tokens } = await oAuth2.getToken(code);
//          fs.writeFileSync("token.json", JSON.stringify(tokens, null, 2));
//          console.log("Token saved to token.json");
//          rl.close();
//        });
//
//      Run:  node generate_token.js
//      Authorise in the browser, paste the code, done.
//
// 4. CREATE .env (optional)
//
//    PORT=3000
//    CREDENTIALS_PATH=credentials.json
//    TOKEN_PATH=token.json
//    LOG_FILE=email_server.log
//
// 5. START THE SERVER
//
//    node mcp_email_server.js
//
// 6. TEST WITH curl
//
//    curl -X POST http://127.0.0.1:3000/send-email \
//      -H "Content-Type: application/json" \
//      -d '{"to":"test@example.com","subject":"Hello","body":"Test email from MCP server"}'
//
//    Health check:
//    curl http://127.0.0.1:3000/health
//
// 7. INTEGRATION WITH TASK PROCESSOR
//
//    Claude Code (via task_processor.py) will call this server ONLY after
//    a task has been moved to Approved/.  The flow:
//
//      1. Watcher creates EMAIL_*.md in Needs_Action/
//      2. TaskProcessor moves to In_Progress/, Claude analyses it
//      3. Claude creates a reply draft in Pending_Approval/
//      4. CEO reviews and moves to Approved/
//      5. ApprovalHandler triggers → Claude calls POST /send-email
//      6. Email sent, task moved to Done/
//
// 8. SECURITY NOTES
//
//    - Server binds to 127.0.0.1 ONLY — not accessible from the network
//    - Every request is logged with timestamp, recipient, and subject
//    - credentials.json and token.json must NEVER be committed to git
//    - Add to .gitignore:
//        credentials.json
//        token.json
//        .env
//        email_server.log
//    - The gmail.send scope allows sending but NOT reading or deleting
//    - Human-in-the-loop: nothing sends without Approved/ workflow
//
