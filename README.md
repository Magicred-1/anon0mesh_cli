# anonmesh

**Mesh First, Chain When It Matters.**

anonmesh is a Python MVP for tunneling Solana JSON-RPC requests over [Reticulum's](https://reticulum.network/) end-to-end encrypted mesh network. Off-grid devices interact with the Solana blockchain through connected gateway nodes ("Beacons") over supported Reticulum transports such as LoRa, WiFi, Packet Radio, and TCP hubs. Desktop BLE relay remains experimental.

When configured, a Beacon can co-sign and submit an `execute_payment` instruction to the **ble_revshare** Anchor program after relaying a transaction with the required Arcium metadata. This logs encrypted payment statistics via [Arcium MPC](https://arcium.com/) without leaking raw amounts.

## Architecture

```
[Client — offline device]
    |  signs tx locally (durable nonce, no blockhash expiry)
    |  sends over Reticulum mesh
    v
[Beacon — internet-connected]
    |  forwards JSON-RPC to Solana
    |  co-signs & sends execute_payment → ble_revshare program
    |                                       → Arcium MPC encrypts amount
    v
[Solana / Arcium devnet]
```

**Beacon (`beacon.py`)** — RPC gateway. Listens as a Reticulum destination, forwards requests to Solana, and after each relayed `sendTransaction` calls `arcium_client.log_payment_stats()` to record encrypted stats on-chain.

**Client (`client.py`)** — Off-grid wallet REPL. Discovers Beacons over the mesh, signs transactions offline with a durable nonce keypair, and relays them without direct internet access.

**Arcium shim (`rescue_shim.mjs`)** — Node.js helper invoked by the beacon. Handles x25519 + RescueCipher encryption of the payment amount, derives Arcium PDAs, and submits the `execute_payment` instruction.

## Quickstart

```bash
git clone https://github.com/anonmesh/anonmesh_cli.git
cd anonmesh_cli
npm install
chmod +x setup.sh
./setup.sh
```

`setup.sh` installs Python dependencies into a local `venv/`, writes a working Reticulum config with public TCP hubs, and generates `run_beacon.sh` / `run_client.sh` launchers.

### Setup flags (non-interactive)

```bash
./setup.sh --beacon          # beacon only
./setup.sh --client          # client only (adds solders, qrcode)
./setup.sh --both            # both
./setup.sh --beacon --systemd       # also install a beacon systemd service
./setup.sh --beacon --ble           # install experimental BLE research deps
./setup.sh --beacon --meshtastic    # install Meshtastic research deps
./setup.sh --beacon --rnode         # configure detected RNode LoRa hardware
./setup.sh --client --wallet-setup  # interactively create wallet + nonce state
./setup.sh --beacon --mainnet       # target Solana mainnet-beta instead of devnet
```

For unattended RNode setup, specify the serial device and legal radio region
explicitly:

```bash
ANONMESH_RNODE_PORT=/dev/ttyUSB0 ANONMESH_RNODE_REGION=us \
  ./setup.sh --beacon --rnode
```

## Configuration

Create `.env` only if you need optional Arcium or wallet overrides:

```bash
touch .env
```

Key variables:

| Variable | Default | Description |
|---|---|---|
| `SOLANA_NETWORK` | `devnet` | Launcher network: `devnet` or `mainnet` |
| `ARCIUM_ENABLED` | `0` | Set to `1` to enable Arcium MPC |
| `ARCIUM_PAYER_KEYPAIR` | *(unset)* | Keypair that pays Arcium computation fees |
| `ARCIUM_RPC_URL` | devnet public endpoint | RPC for Arcium transactions |
| `ARCIUM_MXE_PUBKEY_HEX` | *(unset)* | MXE x25519 public key |
| `ARCIUM_CLUSTER_OFFSET` | `456` | `456` = devnet, `2026` = mainnet-alpha |
| `ARCIUM_BROADCASTER_TOKEN_ACCOUNT` | *(derived)* | Beacon's SPL token account for rev-share |
| `ARCIUM_TREASURY_TOKEN_ACCOUNT` | *(derived from broadcaster)* | Treasury token account |

## Usage

### 1. Start a Beacon

```bash
./run_beacon.sh
```

The beacon prints its **DESTINATION HASH** on startup — share this with clients so they can connect directly. It also auto-announces over the mesh so clients in discovery mode find it automatically.

### 2. Run a Client

```bash
# Auto-discover beacons on the mesh
./run_client.sh

# Connect to a specific beacon hash
./run_client.sh <BEACON_HASH>

# One-shot balance check
./run_client.sh <BEACON_HASH> --balance <SOLANA_ADDRESS>
```

### 3. Run a Headless Exit Node

Use the headless launcher for a laptop or Linux server that only forwards RPC:

```bash
./scripts/headless-node.sh preflight
./scripts/headless-node.sh start
./scripts/headless-node.sh status
./scripts/headless-node.sh logs
./scripts/headless-node.sh stop
```

Override `ANONMESH_CONFIG_DIR`, `ANONMESH_NETWORK`, or `ANONMESH_RPC_URL` when the defaults do not match your deployment.

## Testing

```bash
# Fresh local test environment + unit tests
npm test -- -q

# Localhost Reticulum relay → headless exit node → Solana devnet
.venv-test/bin/python tests/test_tcp_bridge.py
```

## Arcium MPC — execute_payment flow

After the beacon relays a `sendTransaction` containing Arcium metadata, it:

1. Calls `rescue_shim.mjs get_arcium_accounts` to derive all on-chain PDA addresses.
2. Encrypts the payment amount with x25519 + RescueCipher (shim-side).
3. Auto-creates any missing SPL token ATAs (payer, recipient, treasury, broadcaster).
4. Builds a durable-nonce transaction with `execute_payment` on the **ble_revshare** program (`7xeQNUggKc2e5q6AQxsFBLBkXGg2p54kSx11zVainMks`).
5. Co-signs as the broadcaster and submits to Solana.

The `execute_payment` instruction data layout (104 bytes):

```
[discriminator 8B][computation_offset 8B LE][amount 8B LE]
[encrypted_amount 32B][nonce 16B LE][pub_key 32B]
```

### One-time program initialisation

Before `execute_payment` works, the computation definition must be initialised once per deployment:

```bash
ARCIUM_PAYER_KEYPAIR=~/.config/solana/id.json \
ARCIUM_RPC_URL=https://api.devnet.solana.com \
node scripts/init_comp_def_once.mjs
```

### Utility scripts (`scripts/`)

| Script | Purpose |
|---|---|
| `check_arcium_accounts.mjs` | Verify all Arcium PDAs / ATAs exist on devnet |
| `fetch_idl.mjs` | Fetch the deployed program IDL |
| `get_whitelists.js` | List whitelisted mints |
| `init_comp_def_once.mjs` | Initialise the `payment_stats` computation definition |

Run any with:

```bash
node scripts/<script>.mjs [args]
```

## Reticulum configuration

`setup.sh` writes `~/.reticulum/config` automatically. Re-run setup to reset that config. The included [`reticulum_config`](reticulum_config) file is an RNode reference sample; edit its serial port before using it:

```bash
cp reticulum_config ~/.reticulum/config
# Edit the RNode LoRa port for this machine.
```

Public TCP hubs configured by default:

- `rns.beleth.net:4242`
- `dfw.us.g00n.cloud:6969`

RNode LoRa is optional. `setup.sh --ble` installs `bleak` for research only; it does not configure a supported desktop BLE relay. `setup.sh --meshtastic` installs the Python dependency but requires a separately supplied Reticulum interface module. Phone BLE uses the separate Android/iOS native client.

## Dependencies

### Python (managed by `setup.sh` in `venv/`)

| Package | Role |
|---|---|
| `rns` | Reticulum Network Stack |
| `lxmf` | Beacon discovery over the mesh |
| `requests` | Solana RPC calls |
| `solders` | Offline transaction signing (client) |
| `bleak` | Experimental desktop BLE research dependency |
| `meshtastic` | Experimental Meshtastic research dependency |
| `qrcode` | Wallet QR display (optional) |

### Node.js (managed by `npm install`)

| Package | Role |
|---|---|
| `@arcium-hq/client` | Arcium MPC account derivation |
| `@coral-xyz/anchor` | Anchor program interaction |
| `@solana/web3.js` | Solana transaction building |
| `bn.js` | Big-number arithmetic for BN fields |

## Durable nonce transactions

Transactions sent over the mesh can be delayed by minutes or hours. A [durable nonce account](https://docs.solana.com/developing/programming-model/transactions#durable-transaction-nonces) replaces the expiring blockhash so the signed transaction stays valid until it lands on-chain.

`setup.sh --wallet-setup` is intentionally interactive. It generates a signing
keypair and can create a nonce account on your behalf after confirmation
(~0.00145 SOL rent-exempt deposit, recoverable). Review the prompts before using
it with a funded wallet. Generated wallet and nonce keypair JSON files are
written with owner-only `0600` permissions.

The client partially signs the transaction (payer slot); the beacon co-signs (broadcaster slot) before submitting to Solana.

## systemd (beacon auto-start)

```bash
./setup.sh --beacon --systemd

sudo systemctl start anonmesh-beacon
sudo systemctl status anonmesh-beacon
journalctl -u anonmesh-beacon -f
```

`--systemd` requires `sudo`, writes `/etc/systemd/system/anon0mesh-beacon.service`,
and enables the service for boot. Run it only when persistent beacon operation is
intended.
