def count_passed(scores: list[int]) -> int:
    count = 0

    for score in scores:
        if score < 0:
            raise ValueError("score must be non-negative")

        if score >= 50:
            count += 1

    return count