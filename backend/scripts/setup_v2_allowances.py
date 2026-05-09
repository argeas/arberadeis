"""Set pUSD allowances FROM the V2 deposit wallet via the relayer.

Builds a WALLET batch with:
- pUSD.approve(CTF_Exchange_V2, max)
- pUSD.approve(NegRisk_CTF_Exchange_V2, max)
- pUSD.approve(NegRisk_Adapter, max)

Signed by the EOA owner using EIP-712 domain "DepositWallet" v1.
"""

import sys
sys.path.insert(0, '/app')
import json
import time
import httpx
from eth_account import Account
from eth_account.messages import encode_typed_data
from eth_abi import encode
from Crypto.Hash import keccak
from app.config import config

PUSD = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"
DEPOSIT_WALLET = "0x8fe6Ca7d79EB52f0CD1AD214eD344B41A8aF3877"
FACTORY = "0x00000000000Fb5C9ADea0298D729A0CB3823Cc07"
RELAYER_URL = "https://relayer-v2.polymarket.com"

V2_SPENDERS = [
    "0xE111180000d2663C0091e4f400237545B87B996B",  # CTF Exchange V2
    "0xe2222d279d744050d28e00520010520000310F59",  # NegRisk CTF Exchange V2
    "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296",  # NegRisk Adapter
]

MAX_UINT = (1 << 256) - 1


def encode_approve(spender: str, amount: int) -> str:
    """approve(address,uint256) calldata."""
    k = keccak.new(digest_bits=256)
    k.update(b"approve(address,uint256)")
    selector = k.digest()[:4]
    params = encode(["address", "uint256"], [spender, amount])
    return "0x" + selector.hex() + params.hex()


def sign_batch(private_key: str, wallet: str, nonce: int, deadline: int, calls: list) -> str:
    """EIP-712 sign the batch under DepositWallet domain v1."""
    typed_data = {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "Call": [
                {"name": "target", "type": "address"},
                {"name": "value", "type": "uint256"},
                {"name": "data", "type": "bytes"},
            ],
            "Batch": [
                {"name": "wallet", "type": "address"},
                {"name": "nonce", "type": "uint256"},
                {"name": "deadline", "type": "uint256"},
                {"name": "calls", "type": "Call[]"},
            ],
        },
        "primaryType": "Batch",
        "domain": {
            "name": "DepositWallet",
            "version": "1",
            "chainId": 137,
            "verifyingContract": wallet,
        },
        "message": {
            "wallet": wallet,
            "nonce": nonce,
            "deadline": deadline,
            "calls": [
                {"target": c["target"], "value": int(c["value"]), "data": bytes.fromhex(c["data"][2:])}
                for c in calls
            ],
        },
    }
    msg = encode_typed_data(full_message=typed_data)
    signed = Account.sign_message(msg, private_key=private_key)
    return signed.signature.hex()


def get_relay_client():
    from py_builder_relayer_client.client import RelayClient
    from py_builder_signing_sdk.config import BuilderConfig, BuilderApiKeyCreds
    creds = BuilderApiKeyCreds(
        key=config.builder_api_key,
        secret=config.builder_api_secret,
        passphrase=config.builder_api_passphrase,
    )
    return RelayClient(
        relayer_url=RELAYER_URL,
        chain_id=137,
        private_key=config.poly_private_key,
        builder_config=BuilderConfig(local_builder_creds=creds),
    )


def main():
    eoa = config.poly_wallet_address
    print(f"EOA owner: {eoa}")
    print(f"Deposit wallet: {DEPOSIT_WALLET}")
    print()

    # Build approve calls for each V2 spender
    calls = []
    for spender in V2_SPENDERS:
        calldata = encode_approve(spender, MAX_UINT)
        calls.append({"target": PUSD, "value": "0", "data": calldata})
        print(f"  approve({spender[:10]}..., MAX)")

    # Try several nonce types
    relay = get_relay_client()
    nonce = "0"
    for nt in ["WALLET", "wallet", "DEPOSIT_WALLET"]:
        try:
            r = relay._get_request("/nonce", {"address": eoa, "type": nt})
            print(f"Nonce[{nt}]: {r}")
            if isinstance(r, dict) and "nonce" in r:
                nonce = str(r["nonce"])
                break
        except Exception as e:
            print(f"Nonce[{nt}] err: {e}")

    print(f"Using nonce: {nonce}")
    deadline = str(int(time.time()) + 300)

    signature = sign_batch(config.poly_private_key, DEPOSIT_WALLET, int(nonce), int(deadline), calls)
    if not signature.startswith("0x"):
        signature = "0x" + signature
    print(f"Sig: {signature[:30]}... len={len(signature)}")

    payload = {
        "type": "WALLET",
        "from": eoa,
        "to": FACTORY,
        "nonce": str(nonce),
        "signature": signature,
        "depositWalletParams": {
            "depositWallet": DEPOSIT_WALLET,
            "deadline": deadline,
            "calls": calls,
        },
    }

    print("\nSubmitting WALLET batch via relayer client...")
    try:
        result = relay._post_request("POST", "/submit", payload)
        print(f"✅ Response: {result}")
        if isinstance(result, dict):
            print(f"  Tx hash: {result.get('transactionHash')}")
    except Exception as e:
        print(f"❌ Failed: {e}")


if __name__ == "__main__":
    main()
