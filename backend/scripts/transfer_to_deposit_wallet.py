"""Transfer pUSD from old Gnosis Safe proxy to new V2 deposit wallet via relayer.

Uses the Polymarket relayer SAFE batch (gasless) — same mechanism as redemption.
"""

import sys
sys.path.insert(0, '/app')
from web3 import Web3
from eth_abi import encode
from Crypto.Hash import keccak
from app.config import config

PUSD = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"
DEPOSIT_WALLET = "0x8fe6Ca7d79EB52f0CD1AD214eD344B41A8aF3877"


def selector(sig):
    k = keccak.new(digest_bits=256)
    k.update(sig.encode())
    return k.digest()[:4]


def main():
    from py_builder_relayer_client.client import RelayClient, SafeTransaction
    from py_builder_relayer_client.models import OperationType
    from py_builder_signing_sdk.config import BuilderConfig, BuilderApiKeyCreds

    builder_creds = BuilderApiKeyCreds(
        key=config.builder_api_key,
        secret=config.builder_api_secret,
        passphrase=config.builder_api_passphrase,
    )
    builder_config = BuilderConfig(local_builder_creds=builder_creds)
    relay = RelayClient(
        relayer_url="https://relayer-v2.polymarket.com",
        chain_id=137,
        private_key=config.poly_private_key,
        builder_config=builder_config,
    )

    # Check current pUSD balance to know amount
    import httpx
    proxy = config.poly_proxy_address
    padded = proxy[2:].lower().zfill(64)
    bal_call = f"0x70a08231{padded}"
    r = httpx.post("https://polygon-bor-rpc.publicnode.com", json={
        "jsonrpc": "2.0", "method": "eth_call",
        "params": [{"to": PUSD, "data": bal_call}, "latest"], "id": 1
    }, timeout=10)
    amount = int(r.json()["result"], 16)
    print(f"pUSD balance on proxy: ${amount/1e6:.2f}")
    print(f"Transferring to deposit wallet: {DEPOSIT_WALLET}")
    print()

    if amount == 0:
        print("Nothing to transfer.")
        return

    # Build pUSD.transfer(deposit_wallet, amount) calldata
    sel = selector("transfer(address,uint256)")
    params = encode(["address", "uint256"], [Web3.to_checksum_address(DEPOSIT_WALLET), amount])
    calldata = "0x" + sel.hex() + params.hex()

    txn = SafeTransaction(
        to=PUSD,
        operation=OperationType.Call,
        data=calldata,
        value="0",
    )

    print(f"Submitting via relayer SAFE batch (gasless)...")
    try:
        result = relay.execute([txn])
        print(f"✅ Submitted!")
        print(f"  Transaction ID: {result.transaction_id}")
        print(f"  Tx hash: {result.transaction_hash}")
        print(f"  View: https://polygonscan.com/tx/{result.transaction_hash}")
    except Exception as e:
        print(f"❌ Failed: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
