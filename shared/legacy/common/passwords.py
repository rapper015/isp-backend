import bcrypt


def hash_password(plain_text: str) -> str:
    return bcrypt.hashpw(plain_text.encode("utf-8"), bcrypt.gensalt(10)).decode("utf-8")


def verify_password(plain_text: str, password_hash: str) -> bool:
    if not plain_text or not password_hash:
        return False
    try:
        return bcrypt.checkpw(plain_text.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False
