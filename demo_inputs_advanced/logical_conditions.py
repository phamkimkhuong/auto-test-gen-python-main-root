def can_checkout(age: int, verified: bool, total: float, banned: bool) -> str:
    if banned:
        return "blocked"

    if age >= 18 and verified and total >= 100:
        return "priority"

    if age >= 18 and verified and total > 0:
        return "allowed"

    if age < 18 or total <= 0:
        return "blocked"

    return "pending"
