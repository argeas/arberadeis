"""Correct ERC-7739 wrapped signature for Polymarket V2 POLY_1271 (deposit wallet) orders.

Ported from the Rust SDK (rs-clob-client-v2) since py-clob-client-v2 v1.0.1
produces malformed wrapped signatures that fail isValidSignature on the
deposit wallet contract.

Format: 0x + inner_sig(65) + app_domain_sep(32) + contents_hash(32) +
        order_type_string_bytes + uint16_length
"""

from eth_abi import encode
from eth_account import Account
from eth_utils import keccak

# V2 exchange domain
EXCHANGE_DOMAIN_NAME = "Polymarket CTF Exchange"
EXCHANGE_DOMAIN_VERSION = "2"
EXCHANGE_V2 = "0xE111180000d2663C0091e4f400237545B87B996B"
NEG_RISK_EXCHANGE_V2 = "0xe2222d279d744050d28e00520010520000310F59"

# Deposit wallet domain (used in Solady wrapping)
DEPOSIT_WALLET_NAME = "DepositWallet"
DEPOSIT_WALLET_VERSION = "1"

ORDER_TYPE_STRING = (
    "Order(uint256 salt,address maker,address signer,uint256 tokenId,"
    "uint256 makerAmount,uint256 takerAmount,uint8 side,uint8 signatureType,"
    "uint256 timestamp,bytes32 metadata,bytes32 builder)"
)

SOLADY_TYPE_STRING = (
    "TypedDataSign(Order contents,string name,string version,uint256 chainId,"
    "address verifyingContract,bytes32 salt)"
    "Order(uint256 salt,address maker,address signer,uint256 tokenId,"
    "uint256 makerAmount,uint256 takerAmount,uint8 side,uint8 signatureType,"
    "uint256 timestamp,bytes32 metadata,bytes32 builder)"
)

EIP712_DOMAIN_TYPE_STRING = (
    "EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"
)


def _to_bytes32(addr: str) -> bytes:
    """Address as bytes32 (right-padded address)."""
    return bytes.fromhex(addr.lower().replace("0x", "").rjust(64, "0"))


def _addr_to_bytes(addr: str) -> bytes:
    """Address as 20-byte address."""
    return bytes.fromhex(addr.lower().replace("0x", "").rjust(40, "0"))


def app_domain_separator(chain_id: int, verifying_contract: str) -> bytes:
    """Compute EIP-712 domain separator for V2 exchange."""
    return keccak(encode(
        ["bytes32", "bytes32", "bytes32", "uint256", "address"],
        [
            keccak(EIP712_DOMAIN_TYPE_STRING.encode()),
            keccak(EXCHANGE_DOMAIN_NAME.encode()),
            keccak(EXCHANGE_DOMAIN_VERSION.encode()),
            chain_id,
            verifying_contract,
        ],
    ))


def order_struct_hash(order: dict) -> bytes:
    """Compute keccak hash of the Order struct (V2)."""
    return keccak(encode(
        [
            "bytes32", "uint256", "address", "address", "uint256",
            "uint256", "uint256", "uint8", "uint8", "uint256",
            "bytes32", "bytes32",
        ],
        [
            keccak(ORDER_TYPE_STRING.encode()),
            int(order["salt"]),
            order["maker"],
            order["signer"],
            int(order["tokenId"]),
            int(order["makerAmount"]),
            int(order["takerAmount"]),
            int(order["side"]),
            int(order["signatureType"]),
            int(order["timestamp"]),
            bytes.fromhex(order["metadata"][2:]) if isinstance(order["metadata"], str) else order["metadata"],
            bytes.fromhex(order["builder"][2:]) if isinstance(order["builder"], str) else order["builder"],
        ],
    ))


def sign_poly1271_order(private_key: str, order: dict, chain_id: int, neg_risk: bool = False) -> str:
    """
    Build the ERC-7739 wrapped signature for a V2 POLY_1271 (deposit wallet) order.

    Args:
        private_key: hex-encoded private key (0x prefix optional) of the EOA owner
        order: dict with keys: salt, maker, signer, tokenId, makerAmount, takerAmount,
               side, signatureType, timestamp, metadata, builder
        chain_id: 137 for Polygon mainnet
        neg_risk: True if neg-risk market (uses NegRiskCtfExchange V2)

    Returns: hex string (0x...) of the wrapped signature
    """
    verifying_contract = NEG_RISK_EXCHANGE_V2 if neg_risk else EXCHANGE_V2

    contents_hash = order_struct_hash(order)
    app_sep = app_domain_separator(chain_id, verifying_contract)

    # TypedDataSign struct hash (Solady ERC-7739 nested format)
    typed_data_sign_hash = keccak(encode(
        [
            "bytes32",   # SOLADY_TYPE_STRING hash
            "bytes32",   # contents hash (Order)
            "bytes32",   # name hash ("DepositWallet")
            "bytes32",   # version hash ("1")
            "uint256",   # chainId
            "address",   # signer (deposit wallet)
            "bytes32",   # salt (zero)
        ],
        [
            keccak(SOLADY_TYPE_STRING.encode()),
            contents_hash,
            keccak(DEPOSIT_WALLET_NAME.encode()),
            keccak(DEPOSIT_WALLET_VERSION.encode()),
            chain_id,
            order["signer"],
            b"\x00" * 32,
        ],
    ))

    # Build digest: 0x1901 || domain_sep || typed_data_sign_hash
    digest = keccak(b"\x19\x01" + app_sep + typed_data_sign_hash)

    # Sign with EOA private key
    if not private_key.startswith("0x"):
        private_key = "0x" + private_key
    sig_obj = Account._sign_hash(digest, private_key=private_key)

    # 65-byte ECDSA signature
    inner_sig_hex = (
        sig_obj.r.to_bytes(32, "big").hex()
        + sig_obj.s.to_bytes(32, "big").hex()
        + sig_obj.v.to_bytes(1, "big").hex()
    )

    # Wrapped format:
    # 0x + sig(65) + domain_sep(32) + contents_hash(32) + ORDER_TYPE_STRING bytes + uint16 length
    type_string_bytes = ORDER_TYPE_STRING.encode()
    type_length = len(type_string_bytes)
    wrapped = (
        "0x"
        + inner_sig_hex
        + app_sep.hex()
        + contents_hash.hex()
        + type_string_bytes.hex()
        + type_length.to_bytes(2, "big").hex()
    )

    return wrapped
