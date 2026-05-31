#!/usr/bin/env node
/**
 * check_arcium_accounts.mjs — verify all Arcium accounts needed by execute_payment exist on devnet
 * Usage: node check_arcium_accounts.mjs [mint_b58]
 */

import { Connection, PublicKey } from "@solana/web3.js";
import { execFileSync } from "node:child_process";
import { chmodSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const __dir = dirname(fileURLToPath(import.meta.url));
const envPath = join(__dir, "..", ".env");

try {
    chmodSync(envPath, 0o600);
    const envLines = readFileSync(envPath, "utf8").split("\n");
    for (const line of envLines) {
        const t = line.trim();
        if (!t || t.startsWith("#") || !t.includes("=")) continue;
        const [k, ...rest] = t.split("=");
        if (!process.env[k.trim()]) process.env[k.trim()] = rest.join("=").trim();
    }
} catch (err) {
    if (err?.code !== "ENOENT") throw err;
}

const PROGRAM_ID  = "7xeQNUggKc2e5q6AQxsFBLBkXGg2p54kSx11zVainMks";
// Derived: find_program_address([b"ArciumSignerAccount"], MXE_PROGRAM)
const [signPdaPubkey] = PublicKey.findProgramAddressSync(
    [Buffer.from("ArciumSignerAccount")],
    new PublicKey(PROGRAM_ID),
);
const SIGN_PDA = signPdaPubkey.toBase58();
const RPC_URL     = process.env.ARCIUM_RPC_URL || "https://api.devnet.solana.com";
const MINT        = process.argv[2] || "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU";

function redactUrl(raw) {
    try {
        const url = new URL(raw);
        const sensitive = url.username || url.password || url.pathname !== "/"
            || url.search || url.hash;
        return `${url.protocol}//${url.host}${sensitive ? "/..." : ""}`;
    } catch {
        return "<redacted-rpc-url>";
    }
}

const connection  = new Connection(RPC_URL, "confirmed");
const progPubkey  = new PublicKey(PROGRAM_ID);
const mintPubkey  = new PublicKey(MINT);

// Get Arcium account addresses from shim
const shimOut = JSON.parse(
    execFileSync(process.execPath, [join(__dir, "..", "rescue_shim.mjs"),
                                   "arcium_accounts", PROGRAM_ID],
                 { encoding: "utf8" })
);

// Derive whitelist PDA
const [whitelistPda] = PublicKey.findProgramAddressSync(
    [Buffer.from("whitelist"), mintPubkey.toBuffer()], progPubkey
);

const checks = {
    "signPda (derived)":    SIGN_PDA,
    mxeAccount:             shimOut.mxeAccount,
    mempoolAccount:         shimOut.mempoolAccount,
    executingPool:          shimOut.executingPool,
    compDefAccount:         shimOut.compDefAccount,
    clusterAccount:         shimOut.clusterAccount,
    poolAccount:            shimOut.poolAccount,
    clockAccount:           shimOut.clockAccount,
    arciumProgram:          shimOut.arciumProgramId,
    [`whitelistEntry(${MINT.slice(0,8)}…)`]: whitelistPda.toBase58(),
};

console.log(`\nProgram : ${PROGRAM_ID}`);
console.log(`RPC     : ${redactUrl(RPC_URL)}`);
console.log(`Mint    : ${MINT}\n`);

let allGood = true;
for (const [label, address] of Object.entries(checks)) {
    let info;
    try {
        info = await connection.getAccountInfo(new PublicKey(address));
    } catch {
        console.error(`✘ ${label.padEnd(32)} RPC request failed`);
        allGood = false;
        continue;
    }
    const ok   = info !== null;
    const size = ok ? `${info.data.length}B` : "NOT FOUND";
    console.log(`${ok ? "✔" : "✘"} ${label.padEnd(32)} ${address.slice(0,20)}…  ${size}`);
    if (!ok) allGood = false;
}

console.log(allGood
    ? "\n✔ All accounts present — ready to execute_payment"
    : "\n✘ Missing accounts above must be initialized first");
if (!allGood) process.exitCode = 1;
