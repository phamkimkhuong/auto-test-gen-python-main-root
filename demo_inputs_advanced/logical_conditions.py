# nhiều điều kiện logic
# Tổ hợp and, or, biến trạng thái như verified, banned, priority, age, total.
def evaluate_promotion_eligibility(age, verified, banned, total, priority):
    if age < 18:
        return "underage"

    if banned:
        return "blocked"

    if verified and (total >= 500 or priority):
        return "premium"

    if verified or total >= 200:
        return "standard"

    return "limited"
