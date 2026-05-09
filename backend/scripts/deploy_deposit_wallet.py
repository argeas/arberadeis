"""Deploy a Polymarket V2 deposit wallet via the relayer (gasless).

Steps:
1. POST /submit with {type: WALLET-CREATE} to relayer
2. Poll /transaction?id=... until deployed
3. Print the deposit wallet address

This unlocks V2 trading: signature_type=3 (POLY_1271) with funder=deposit_wallet.
"""

import sys
sys.path.insert(0, '/app')
import time
from app.config import config

DEPOSIT_WALLET_FACTORY = "0x00000000000Fb5C9ADea0298D729A0CB3823Cc07"


def main():
    from py_builder_relayer_client.client import RelayClient
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

    signer_addr = relay.signer.address()
    print(f"Owner (EOA signer): {signer_addr}")
    print(f"Existing proxy:     {config.poly_proxy_address}")
    print()

    # Submit WALLET-CREATE manually since the v0.0.1 lib doesn't have it built-in
    body = {
        "type": "WALLET-CREATE",
        "from": signer_addr,
        "to": DEPOSIT_WALLET_FACTORY,
    }

    print(f"Submitting WALLET-CREATE to relayer...")
    print(f"Payload: {body}")
    print()

    try:
        resp = relay._post_request("POST", "/submit", body)
        print(f"Response: {resp}")
        tx_id = resp.get("transactionID")
        if tx_id:
            print(f"\nTransaction ID: {tx_id}")
            print(f"Polling for completion...")
            for i in range(60):
                time.sleep(2)
                tx = relay.get_transaction(tx_id)
                state = tx.get("state") if isinstance(tx, dict) else None
                print(f"  [{i*2}s] state={state}")
                if state in ("STATE_EXECUTED", "STATE_MINED", "EXECUTED", "MINED"):
                    print(f"\n✅ Deployed!")
                    print(f"Tx hash: {tx.get('transactionHash')}")
                    print(f"Deposit wallet: {tx.get('depositWallet') or tx.get('to') or '(check polygonscan)'}")
                    print(f"View: https://polygonscan.com/tx/{tx.get('transactionHash')}")
                    return
                if state in ("STATE_FAILED", "FAILED", "STATE_CANCELED"):
                    print(f"\n❌ Failed: {tx}")
                    return
    except Exception as e:
        print(f"❌ Relayer error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
