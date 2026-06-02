# Bug điều kiện biên
def calculate_grade_buggy(score):
    if score < 0 or score > 100:
        raise ValueError("score must be between 0 and 100")

    if score > 90:  # BUG: 90 nên đạt A, nhưng bị rơi xuống B
        return "A"

    if score >= 75:
        return "B"

    if score >= 50:
        return "C"

    return "F"