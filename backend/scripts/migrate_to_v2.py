"""One-time migration script: USDC.e → pUSD via Polymarket Relayer (gasless).

Builds two SafeTransactions and submits them in a single relayer batch:
1. Approve CollateralOnramp to spend USDC.e (max approval)
2. Call wrap() to convert USDC.e to pUSD

Uses the same relayer that polybot uses for redemption — no MATIC needed.
"""

import sys
sys.path.insert(0, '/app')
from web3 import Web3
from eth_abi import encode
from Crypto.Hash import keccak

from app.config import config

USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
ONRAMP = "0x93070a847efEf7F70739046A929D47a521F5B8ee"
PUSD = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"
POLYGON_RPC = "https://polygon-bor-rpc.publicnode.com"

ERC20_ABI = [
    {"name": "balanceOf", "type": "function", "inputs": [{"name": "account", "type": "address"}], "outputs": [{"type": "uint256"}], "stateMutability": "view"},
    {"name": "allowance", "type": "function", "inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}], "outputs": [{"type": "uint256"}], "stateMutability": "view"},
]


def selector(sig: str) -> bytes:
    k = keccak.new(digest_bits=256)
    k.update(sig.encode())
    return k.digest()[:4]


def build_approve_txn(amount: int):
    """Build SafeTransaction: USDC.e.approve(ONRAMP, amount)"""
    from py_builder_relayer_client.client import SafeTransaction
    from py_builder_relayer_client.models import OperationType
    sel = selector("approve(address,uint256)")
    params = encode(["address", "uint256"], [Web3.to_checksum_address(ONRAMP), amount])
    calldata = "0x" + sel.hex() + params.hex()
    return SafeTransaction(to=USDC_E, operation=OperationType.Call, data=calldata, value="0")


def build_wrap_txn(proxy_addr: str, amount: int):
    """Build SafeTransaction: ONRAMP.wrap(USDC_E, proxy, amount)"""
    from py_builder_relayer_client.client import SafeTransaction
    from py_builder_relayer_client.models import OperationType
    sel = selector("wrap(address,address,uint256)")
    params = encode(
        ["address", "address", "uint256"],
        [Web3.to_checksum_address(USDC_E), Web3.to_checksum_address(proxy_addr), amount],
    )
    calldata = "0x" + sel.hex() + params.hex()
    return SafeTransaction(to=ONRAMP, operation=OperationType.Call, data=calldata, value="0")


def get_relay_client():
    from py_builder_relayer_client.client import RelayClient
    from py_builder_signing_sdk.config import BuilderConfig, BuilderApiKeyCreds
    builder_creds = BuilderApiKeyCreds(
        key=config.builder_api_key,
        secret=config.builder_api_secret,
        passphrase=config.builder_api_passphrase,
    )
    builder_config = BuilderConfig(local_builder_creds=builder_creds)
    return RelayClient(
        relayer_url="https://relayer-v2.polymarket.com",
        chain_id=137,
        private_key=config.poly_private_key,
        builder_config=builder_config,
    )


def main():
    w3 = Web3(Web3.HTTPProvider(POLYGON_RPC))
    proxy = Web3.to_checksum_address(config.poly_proxy_address)

    usdc = w3.eth.contract(address=Web3.to_checksum_address(USDC_E), abi=ERC20_ABI)
    pusd = w3.eth.contract(address=Web3.to_checksum_address(PUSD), abi=ERC20_ABI)

    usdc_bal = usdc.functions.balanceOf(proxy).call()
    pusd_bal = pusd.functions.balanceOf(proxy).call()

    print(f"Proxy: {proxy}")
    print(f"USDC.e: ${usdc_bal/1e6:.2f}")
    print(f"pUSD:   ${pusd_bal/1e6:.2f}")
    print()

    if usdc_bal == 0:
        print("Nothing to migrate.")
        return

    print(f"Migrating ${usdc_bal/1e6:.2f} USDC.e → pUSD via relayer (gasless)...")

    relay = get_relay_client()
    approve_txn = build_approve_txn(usdc_bal)
    wrap_txn = build_wrap_txn(proxy, usdc_bal)

    print(f"  Built {len([approve_txn, wrap_txn])} transactions")
    print(f"  Submitting batch...")

    try:
        result = relay.execute([approve_txn, wrap_txn])
        print(f"  ✅ Success!")
        print(f"  Transaction ID: {result.transaction_id}")
        print(f"  Tx hash: {result.transaction_hash}")
        print(f"  View: https://polygonscan.com/tx/{result.transaction_hash}")
    except Exception as e:
        print(f"  ❌ Relayer failed: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
